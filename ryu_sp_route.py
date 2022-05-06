
# 运行在RYU控制器，使用最短路算法对数据包进行路由并给交换机添加流表
from ryu.base import app_manager
from ryu import utils
from ryu.controller import mac_to_port
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, HANDSHAKE_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.controller.event import EventRequestBase, EventReplyBase
from ryu.ofproto import ofproto_v1_3
from ryu.lib.mac import haddr_to_bin
from ryu.lib.packet import packet
from ryu.lib.packet import ethernet,ipv4,icmp,ipv6
from ryu.lib.packet import ether_types
from ryu.lib.packet import  in_proto as inet
from ryu.lib.packet import tcp,udp
from ryu.lib import mac
from ryu.topology.api import get_switch, get_link,get_all_link
from ryu.app.wsgi import ControllerBase
from ryu.topology import event, switches
import networkx as nx
import random 
from ryu.lib import hub
from re import T
import numpy as np
import matplotlib.pyplot as plt
import time
import queue
import logging
import copy
import pickle
import setting
from setting import SFC
import requests

MASTER_IP = "10.1.1.123"
MASTER_PORT = "6000"

topo_name=setting.topo#例如'shy_test_100_sw'
user_name=setting.user_name#例如'sw'

# class SFC(object):
#     def __init__(self,tsp:int,user:int,comp_lst:list,link_lst:list):  #the requestor,the source server ,the compute resource demand,the link resource demand
#         self.tsp=tsp# path start
#         self.user=user#prth end
#         self.comp_lst=comp_lst
#         self.link_lst=link_lst
#     def show(self):
#         return 'tsp is %d,user is %d,com_lst is %s,link_lst is %s'%(self.tsp,self.user,str(self.comp_lst),str(self.link_lst))



class ShortestPath(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(ShortestPath, self).__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.topology_api_app = self
        self.net=nx.DiGraph()#用于形成网络拓扑
        self.switch=set()#dpid的集合
        self.found_link=0#网络交换机网络的已经发现的链路
        self.monitor_port={}#监控端口switch_dpid:{port1 port2}
        self.dst_dpid={}#(src_switch_dpid,port):dst_dpid
        self.nodes = {}
        self.links = {}
        self.switch_adj_lst , self.user_sw_dic , self.switch_comp_dic = self.read_topo_file()
        self.switch_lst=[i for i in range(len(self.switch_adj_lst))]
        self.user_lst=[i for i in range(len(self.user_sw_dic))]
        
        self.sw_dpid=self.get_switch_dpid(user_name,topo_name) #{'s1': '0000267738265943'}
        self.host_mac=self.get_host_mac(user_name,topo_name)#{'h1': '56:3b:e7:77:2c:b6', 'h2': '9a:eb:9d:d0:bc:a0'}
        
        self.changed_link=copy.deepcopy(self.switch_adj_lst)#用于记录剩余的带宽
        self.changed_comp=copy.deepcopy(self.switch_comp_dic)#用于记录剩余的计算资源
        self.forbid_edge=self.kruskal(self.switch_adj_lst)#最小生成树中的禁止边
        self.forbid_port=dict()#self.forbid_port[dpid]={}

        self.no_of_nodes = 0
        self.no_of_links = sum([1 if j >0 else 0   for i in self.switch_adj_lst for j in i ])#表示所有交换机链路的数量
        self.sfc_lst=self.read_sfc_file()
        self.path=[0 for n in range(len(self.sfc_lst))]#在这里初始化sfc请求和相应的路
        self.stopped=False#拓扑结束
        self.topo_thread=hub.spawn(self._discover)#发现链路的协程

        
    def get_the_link(self,app, dpid=None):
        rep = app.send_the_request(event.EventLinkRequest(dpid))
        print('wait for link')
        return rep.links
    
    def get_the_switch(self,app, dpid=None):
        rep = app.send_the_request(event.EventSwitchRequest(dpid))
        print('wait for switch')
        return rep.switches if rep else []
    
    # def _send_the_event(self, ev, state):
    #     self._events_sem.acquire()
    #     self.events.put((ev, state),block=True,timeout=1)

    # def send_the_event(self, name, ev, state=None):
    #     if name in SERVICE_BRICKS:
    #         if isinstance(ev, EventRequestBase):
    #             ev.src = self.name
    #         LOG.debug("EVENT %s->%s %s",
    #                   self.name, name, ev.__class__.__name__)
    #         SERVICE_BRICKS[name]._send_the_event(ev, state)
    #     else:
    #         LOG.debug("EVENT LOST %s->%s %s",
    #                   self.name, name, ev.__class__.__name__)
    
    def send_the_request(self, req):
        assert isinstance(req, EventRequestBase)
        req.sync = True
        req.reply_q = hub.Queue()
        print('sending request')
        # self.send_the_event(req.dst, req)
        self.send_event(req.dst, req)
        # going to sleep for the reply
        print('wait for reply')
        try:
            reply=req.reply_q.get(block=True,timeout=1)#阻塞，超时设置为1秒
            print('reply ok')
            return reply
        except queue.Empty:
            print('empty')
            return
        
        
    def _discover(self):
        '''用于发现拓扑'''
        while len(self.switch)!=len(self.switch_adj_lst):#用于发现所有交换机
            switch_list = self.get_the_switch(self.topology_api_app, None) 
            for switch in switch_list:
                self.switch.add(switch.dp.id)
            print('now found sw node is ',len(self.switch))
            self.net.add_nodes_from(list(self.switch))
            hub.sleep(2)
        while True:
            print('all link should be  ',self.no_of_links+len(self.user_sw_dic)*2)
            print('num of sw link should be ',self.no_of_links)
            print('network found link is ',len(self.net.edges()))
            print('found sw link is ',self.found_link)
            
            if len(self.net.edges()) != self.no_of_links+len(self.user_sw_dic)*2:#网络拓扑没有发现完全
                # links_list = get_link(self.topology_api_app, None)
                links_list=self.get_the_link(self.topology_api_app,None)
                print(0)
                self.found_link=len(links_list)
                links=[(link.src.dpid,link.dst.dpid,{'port':link.src.port_no}) for link in links_list]
                self.net.add_edges_from(links) 
                links=[(link.dst.dpid,link.src.dpid,{'port':link.dst.port_no}) for link in links_list]
                self.net.add_edges_from(links)
                hub.sleep(1)
                
                if len(self.net.edges())==self.no_of_links and len(self.net._node)==len(self.switch_adj_lst):
                    for item in self.net._node.items():
                        self.forbid_port[item[0]]=set()#存储禁止端口
                        self.monitor_port[item[0]]=set()
                    for link in self.net.edges():#存储需要监控的端口
                        self.monitor_port[link[0]].add(self.net._succ[link[0]][link[1]]['port'])
                        self.dst_dpid[(link[0],self.net._succ[link[0]][link[1]]['port'])]=link[1]
                        if (link[0],link[1]) in self.forbid_edge or (link[1],link[0]) in self.forbid_edge:
                            self.forbid_port[link[0]].add(self.net._succ[link[0]][link[1]]['port'])    
                    if sum([len(v[1]) for v in self.forbid_port.items()])==2*len(self.forbid_edge):
                        print('forbid port ok')
            else:#网络发现完全，停止协程
                print('kill the discover thread')
                hub.kill(self.topo_thread)

    def read_topo_file(self):
        '''读入拓扑信息'''
        with open('topo_info.txt','rb') as f:
            topo_info=pickle.load(f)
        # print('topo_info:',topo_info)
        switch_adj_lst=topo_info['switch_adj_lst']
        user_sw_dic=topo_info['user_sw_dic']
        switch_comp_dic=topo_info['switch_comp_dic']
        return switch_adj_lst,user_sw_dic,switch_comp_dic

    #从sfc_lst.txt文件中读入sfc_lst.
    def read_sfc_file(self):
        with open('sfc_lst.txt','rb') as f:
            try:
                sfc_lst=pickle.load(f)
                # print(sfc_lst[0].show())
                return sfc_lst
            except EOFError():
                print('empty sfc_lst.txt')
                
    #kruskal算法，返回禁止洪泛的边，参数是带宽由大到小
    def kruskal(self,switch_adj_lst):#注意返回的是sw_dpid
        def find_set(node,set_node,node_num):
            for i in range(node_num):
                if(node in set_node[i]):
                    return i
        
        def union(set1,set2,set_node):
            set_node[set1]=set_node[set1]+set_node[set2]
            set_node[set2]=[]

        forbid_edge=[]#forbid_edge[i]=(1,2),边（1，2）被禁止
        all_edge=dict()#all_edge[(1,2)]=5,边（1，2）带宽为5
        sw_num=len(switch_adj_lst)
        set_node=dict()
        for i in range(sw_num):#集合i包括的点有[i]
            set_node[i]=[i]

        for i in range(sw_num):
            for j in range(sw_num):
                if(i>=j or switch_adj_lst[i][j]==0):
                    continue
                all_edge[(i,j)]=switch_adj_lst[i][j]
        sort_edge=[v[0] for v in sorted(all_edge.items(),key=lambda item:item[1],reverse=True)]
        for edge in sort_edge:
            set1=find_set(edge[0],set_node,sw_num)
            set2=find_set(edge[1],set_node,sw_num)
            if(set1==set2):#加入此边会成环
                forbid_edge.append(edge)
            else:
                union(set1,set2,set_node)
        forbid_edge=[(v[0]+1,v[1]+1) for v in forbid_edge]#交换机名字从s1开始
        forbid_dpid=[]
        datapath_dpid=self.sw_dpid
        for item in forbid_edge:
            forbid_dpid.append(( int('0x'+datapath_dpid['s'+str(item[0])],16) , int('0x'+datapath_dpid['s'+str(item[1])],16)))
        print('forbid dpid is',forbid_dpid)
        print('forbid edge is ',forbid_edge)
        return forbid_dpid

    # #给数据包找路，并消耗相应资源
    # def req_proc(self,sfc:SFC,short_len,user_sw_dic,switch_adj_lst,changed_link_lst,changed_comp_dic):
    #     def cmp(list1,list2):
    #         if(len(list1)!=len(list2)):
    #             # print('error two list len is not 相同')
    #             return None
    #         for i in range(len(list1)):
    #             if(list1[i]<=list2[i]):
    #                 continue
    #             else:
    #                 return False
    #         return True
    #     def allpath(switch_adj_lst,s ,e,short_len,path=[]):
    #         path=path+[s]
    #         if(s==e):
    #             return [path]
    #         paths=[]
    #         for i in range(len(switch_adj_lst)):
    #             if(i not in path and switch_adj_lst[s][i]!=0 and len(path)<short_len):   #4是路径最短路长度
    #                 ns=allpath(switch_adj_lst,i,e,short_len,path)
    #                 for n in ns:
    #                     paths.append(n)
    #         return paths
    #     def sp_path(sfc: SFC,short_len,user_switch_dic, switch_adj_lst, changed_link_lst, changed_comp_dic):
    #         if short_len<len(sfc.comp_lst):
    #             return None
    #         sort_path=list(filter(lambda v:len(v)==short_len,allpath(switch_adj_lst,user_switch_dic[sfc.tsp],user_switch_dic[sfc.user],short_len,[])))
    #         for path in sort_path:
    #             if len(path)<len(sfc.comp_lst):
    #                 continue
    #             elif len(path)==len(sfc.comp_lst):
    #                 path_link=[]
    #                 path_comp=[changed_comp_dic[v] for v in path]
    #                 for i in range(len(path)-1):
    #                     path_link.append(changed_link_lst[path[i]][path[i+1]])
    #                 if cmp(sfc.comp_lst,path_comp) and cmp(sfc.link_lst,path_link):
    #                     return path
    #             else:
    #                 exten=len(path)-len(sfc.comp_lst)
    #                 min_link=min(sfc.link_lst)
    #                 min_link_index=sfc.link_lst.index(min_link)
    #                 my_comp=copy.deepcopy(sfc.comp_lst)
    #                 my_link=copy.deepcopy(sfc.link_lst)
    #                 for i in range(exten):
    #                     my_comp.insert(min_link_index+1,0)
    #                     my_link.insert(min_link_index,min_link)
    #                 path_link=[]
    #                 path_comp=[changed_comp_dic[v] for v in path]
    #                 for i in range(len(path)-1):
    #                     path_link.append(changed_link_lst[path[i]][path[i+1]])
    #                 if cmp(my_comp,path_comp) and cmp(my_link,path_link):
    #                     sfc.comp_lst=my_comp
    #                     sfc.link_lst=my_link
    #                     return path
    #         return None
        
    #     vnf_lst=sp_path(sfc,short_len,user_sw_dic,switch_adj_lst,changed_link_lst,changed_comp_dic)
    #     if vnf_lst==None:
    #         print('no path')
    #     else:
    #         print('have fangan')
    #         # print(vnf_lst)
    #         # print(sfc.tsp,'->',sfc.user)
    #         # print('comp:',sfc.comp_lst)
    #         # print('link:',sfc.link_lst)
    #         for i in range(len(vnf_lst)):
    #             # print(i,changed_comp_dic[vnf_lst[i]],sfc.comp_lst[i])
    #             changed_comp_dic[vnf_lst[i]]=changed_comp_dic[vnf_lst[i]] - sfc.comp_lst[i]
    #             # print(changed_comp_dic[vnf_lst[i]])
    #         for i in range(len(vnf_lst)-1):
    #             # print(i,changed_link_lst[vnf_lst[i]][vnf_lst[i+1]],sfc.link_lst[i])
    #             changed_link_lst[vnf_lst[i]][vnf_lst[i+1]]=changed_link_lst[vnf_lst[i]][vnf_lst[i+1]] - sfc.link_lst[i]
    #             changed_link_lst[vnf_lst[i+1]][vnf_lst[i]]=changed_link_lst[vnf_lst[i]][vnf_lst[i+1]]
    #             # print( changed_link_lst[vnf_lst[i+1]][vnf_lst[i]])
    #     return vnf_lst


        #给数据包找路，并消耗相应资源
    def req_proc(self,sfc:SFC,sfc_len,user_sw_dic,switch_adj_lst,changed_link_lst,changed_comp_dic):
        def cmp(list1,list2):
            if(len(list1)!=len(list2)):
                # print('error two list len is not 相同')
                return None
            for i in range(len(list1)):
                if(list1[i]<=list2[i]):
                    continue
                else:
                    return False
            return True
        def allpath(switch_adj_lst,s ,e,sfc_len,path=[]):
            path=path+[s]
            if(s==e):
                return [path]
            paths=[]
            for i in range(len(switch_adj_lst)):
                if(i not in path and switch_adj_lst[s][i]!=0 and len(path)<sfc_len):   #4是路径最短路长度
                    ns=allpath(switch_adj_lst,i,e,sfc_len,path)
                    for n in ns:
                        paths.append(n)
            return paths
        def sp_path(sfc: SFC,sfc_len,user_switch_dic, switch_adj_lst, changed_link_lst, changed_comp_dic):
            sort_path=list(filter(lambda v:len(v)==sfc_len,allpath(switch_adj_lst,user_switch_dic[sfc.tsp],user_switch_dic[sfc.user],sfc_len,[])))
            for path in sort_path:
                if len(path)==len(sfc.comp_lst):
                    path_link=[]
                    path_comp=[changed_comp_dic[v] for v in path]
                    for i in range(len(path)-1):
                        path_link.append(changed_link_lst[path[i]][path[i+1]])
                    if cmp(sfc.comp_lst,path_comp) and cmp(sfc.link_lst,path_link):
                        print(path)
                        return path
            return None
        
        vnf_lst=sp_path(sfc,sfc_len,user_sw_dic,switch_adj_lst,changed_link_lst,changed_comp_dic)
        if vnf_lst==None:
            print('no path')
        else:
            print('have fangan')
            # print(vnf_lst)
            # print(sfc.tsp,'->',sfc.user)
            # print('comp:',sfc.comp_lst)
            # print('link:',sfc.link_lst)
            for i in range(len(vnf_lst)):
                # print(i,changed_comp_dic[vnf_lst[i]],sfc.comp_lst[i])
                changed_comp_dic[vnf_lst[i]]=changed_comp_dic[vnf_lst[i]] - sfc.comp_lst[i]
                # print(changed_comp_dic[vnf_lst[i]])
            for i in range(len(vnf_lst)-1):
                # print(i,changed_link_lst[vnf_lst[i]][vnf_lst[i+1]],sfc.link_lst[i])
                changed_link_lst[vnf_lst[i]][vnf_lst[i+1]]=changed_link_lst[vnf_lst[i]][vnf_lst[i+1]] - sfc.link_lst[i]
                changed_link_lst[vnf_lst[i+1]][vnf_lst[i]]=changed_link_lst[vnf_lst[i]][vnf_lst[i+1]]
                # print( changed_link_lst[vnf_lst[i+1]][vnf_lst[i]])
        return vnf_lst


    # Handy function that lists all attributes in the given object
    def ls(self,obj):
        print("\n".join([x for x in dir(obj) if x[0] != "_"]))
    #将int值也就是矩阵的索引值转换为DPID
    def trans_int_dpid(self,vnf_lst:list,src:str,dst:str):
        datapath_dpid=self.sw_dpid
        # print('vnf_lst is ',vnf_lst)
        for i in range(len(vnf_lst)):
            vnf_lst[i]=int('0x'+datapath_dpid['s'+str(vnf_lst[i]+1)],16)
        
        vnf_lst.append(dst)
        vnf_lst.insert(0,src)
        # print(vnf_lst)
        return vnf_lst
    #确定这个数据包是否是SFC事件中的,如果是，返回索引，否则返回-1
    def find_sfc(self,src:str,dst:str,sfc_lst:list):
        mac_host={v:k for k,v in self.host_mac.items()}
        tsp=int(mac_host[src][1:])-1
        user=int(mac_host[dst][1:])-1
        for i in range(len(sfc_lst)):
            #以应对TCP的确认
            if((sfc_lst[i].tsp==tsp and sfc_lst[i].user==user) or (sfc_lst[i].tsp==user and sfc_lst[i].user==tsp)):
                return i
            #使用UDP，不需要确认包的回传
            # if(sfc_lst[i].tsp==tsp and sfc_lst[i].user==user):
            #     return i
        return -1
    

    #添加流表项
    def add_flow(self, datapath, match, actions,buffer_id=None):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        if buffer_id:
            mod = datapath.ofproto_parser.OFPFlowMod(
                datapath=datapath, match=match, cookie=0,
                command=ofproto.OFPFC_ADD, idle_timeout=0, hard_timeout=0,
                priority=1, buffer_id=buffer_id,instructions=inst)
        else:
            mod = datapath.ofproto_parser.OFPFlowMod(
                datapath=datapath, match=match, cookie=0,
                command=ofproto.OFPFC_ADD, idle_timeout=0, hard_timeout=0,
                instructions=inst, priority=1)
        datapath.send_msg(mod)


#             idle_timeout (0)

# Flow Entry 的有效期限，以秒為單位。Flow Entry 如果未被參照而且超過了指定的時間之後， 該 Flow Entry 將會被刪除。如果 Flow Entry 有被參照，則超過時間之後會重新歸零計算。

# 在 Flow Entry 被刪除之後就會發出 Flow Removed 訊息通知 Controller 。

# hard_timeout (0)

# Flow Entry 的有效期限，以秒為單位。跟 idle_timeout 不同的地方是， hard_timeout 在超過時限後並不會重新歸零計算。 也就是說跟 Flow Entry 與有沒有被參照無關，只要超過指定的時間就會被刪除。

# 跟 idle_timeout 一樣，當 Flow Entry 被刪除時，Flow Removed 訊息將會被發送來通知 Controller。


    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]
        if(pkt.get_protocol(ipv6.ipv6)):
            return
        # pkt_ipv4=pkt.get_protocol(ipv4.ipv4)
        # ip_src=pkt_ipv4.src
        # ip_dst=pkt_ipv4.dst
        pkt_tcp=pkt.get_protocol(tcp.tcp)
        tcp_dst=None
        if pkt_tcp!=None:
            tcp_dst=pkt_tcp.dst_port
        pkt_udp=pkt.get_protocol(udp.udp)
        pkt_icmp=pkt.get_protocol(icmp.icmp)
        shortest=False#指示是否应用的是最短路

        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            # ignore lldp packet
            return

        dst = eth.dst#type is str
        src = eth.src
        dpid = datapath.id#是十进制整数
        self.mac_to_port.setdefault(dpid, {})
        #print(src,dst)
        #print "nodes"
        #print self.net.nodes()
        #print "edges"
        #print self.net.edges()
        # self.logger.info("packet in %s %s %s %s", dpid, src, dst, in_port)
        if src not in self.net:#将主机加入网络
            self.net.add_node(src)
            self.net.add_edge(dpid,src,port=in_port,weight=0)
            self.net.add_edge(src,dpid,weight=0)
        out_port=None
        if dst in self.net:
            # print('dst in:',src,dst,dpid)

            # if(pkt_icmp or pkt_tcp or eth.ethertype==ether_types.ETH_TYPE_ARP):
            if(pkt_icmp or eth.ethertype==ether_types.ETH_TYPE_ARP):
                try:
                    path = nx.shortest_path(self.net, src, dst, weight="unweighted")#使用最短路算法找路
                    # print ("dpid=", dpid)
                    # print('path is ',path)
                    # print('all switch is ',self.switch)
                    #print ("length=", nx.shortest_path_length(self.net, src, dst, weight="weight"))
                    next = path[path.index(dpid) + 1]#下一跳
                    out_port = self.net[dpid][next]['port']#出端口
                    # print('0-src and dst is: ',src,dst)
                    # print('dpid and next is ',dpid,next)
                    shortest=True
                except nx.NetworkXNoPath or ValueError or KeyError:
                    # out_port = ofproto.OFPP_FLOOD
                    # print('path is ',path)
                    # print('all switch is ',self.switch)
                    # print('nopath or error--flood')
                    return

            else:
                pk_index=self.find_sfc(src,dst,self.sfc_lst)
                if(pk_index==-1):
                    out_port=ofproto.OFPP_LOCAL
                    # print('it is not sfc')
                else:
                    if(self.path[pk_index]!=0):#已经有路
                        # print('path have exist')
                        path=self.path[pk_index]
                        # print('path is ',path)
                        next=None
                        
                        try:
                            if(path[0]==src):
                                next=path[path.index(dpid)+1]
                            elif(path[0]==dst):
                                next=path[path.index(dpid)-1]
                            out_port=self.net[dpid][next]['port']
                            # print(pkt)
                            # print(self.net[dpid][next])
                            # print('1-src and dst is: ',src,dst)
                            # print('path is ',path)
                            # print('dpid and next and outport is ',dpid,next,out_port)
                        except KeyError:
                            # print('1-key error')
                            # print('src and dst is: ',src,dst)
                            # print('path is ',path)
                            # print('dpid and next and outport is ',dpid,next,out_port)
                            # out_port = ofproto.OFPP_FLOOD
                            return
                    else:
                        # print('path is computing')
                        # vnf_lst=self.req_proc(self.sfc_lst[pk_index],\
                        #     len(nx.shortest_path(self.net, src, dst, weight="unweighted"))-2,\
                        #     self.user_sw_dic,self.switch_adj_lst,self.changed_link,self.changed_comp)
                        vnf_lst=self.req_proc(self.sfc_lst[pk_index],\
                            len(self.sfc_lst[pk_index].comp_lst),\
                            self.user_sw_dic,self.switch_adj_lst,self.changed_link,self.changed_comp)
                        print('path compute ok')
                        if(vnf_lst==None):
                            out_port=ofproto.OFPP_LOCAL
                            # print('no path')
                        else:
                            path=self.trans_int_dpid(vnf_lst,src,dst)
                            next=None
                            self.path[pk_index]=path
                            try:
                                if(path[0]==src):
                                    next=path[path.index(dpid)+1]
                                elif(path[0]==dst):
                                    next=path[path.index(dpid)-1]
                                out_port=self.net[dpid][next]['port']
                                # print('2-src and dst is: ',src,dst)
                                # print('path is ',path)
                                # print('dpid and next is ',dpid,next)
                            except KeyError:
                                # print('2-key error')
                                # print('src and dst is: ',src,dst)
                                # print('path is ',path)
                                # print('dpid and next is ',dpid,next)
                                # out_port = ofproto.OFPP_FLOOD
                                return
        else:
            out_port = ofproto.OFPP_FLOOD

        actions = [parser.OFPActionOutput(out_port)]
        match = parser.OFPMatch(in_port=in_port,eth_type=ether_types.ETH_TYPE_IP,ip_proto=inet.IPPROTO_TCP,eth_src=src,eth_dst=dst,tcp_dst=tcp_dst)
        if out_port==None:
            actions=[]
        
        
        
        if out_port != ofproto.OFPP_FLOOD and shortest==False and out_port !=ofproto.OFPP_LOCAL:
            if msg.buffer_id != ofproto.OFP_NO_BUFFER:
                self.add_flow(datapath, match, actions,msg.buffer_id)
                return 
            else:
                self.add_flow(datapath,match,actions)
        
        elif out_port==ofproto.OFPP_LOCAL:
            if msg.buffer_id != ofproto.OFP_NO_BUFFER:
                self.add_flow(datapath, match, actions,msg.buffer_id)
                return 
            else:
                self.add_flow(datapath,match,actions)
        
        
        
        
        
        # install a flow which use OSPF algorithm to avoid packet_in next time
        # if out_port != ofproto.OFPP_FLOOD and shortest==False and out_port !=ofproto.OFPP_LOCAL:
        #     match_udp = parser.OFPMatch(in_port=in_port,eth_type=ether_types.ETH_TYPE_IP,ip_proto=inet.IPPROTO_UDP,eth_src=src,eth_dst=dst)
        #     match_tcp = parser.OFPMatch(in_port=in_port,eth_type=ether_types.ETH_TYPE_IP,ip_proto=inet.IPPROTO_TCP,eth_src=src,eth_dst=dst)
            
        #     # print('inport:',in_port)
        #     if msg.buffer_id != ofproto.OFP_NO_BUFFER:
        #         self.add_flow(datapath, match_udp, actions,msg.buffer_id)
        #         self.add_flow(datapath, match_tcp, actions,msg.buffer_id)
        #         # self.add_flow(datapath, match, actions,msg.buffer_id)
        #         # print('1switch -id:', dpid)
        #         return 
        #     else:
        #         # self.add_flow(datapath,match,actions)
        #         self.add_flow(datapath,match_udp,actions)
        #         self.add_flow(datapath,match_tcp,actions)
        #         # print(msg.reason)#=0,即OFPR_NO_MATCH，
        #         # print(src,dst,actions,in_port,out_port)
        #         # print('2switch -id:', dpid)
            
        #     # self.send_barrier_request(datapath)
        #     # print('保证add_flow执行')
        
        # elif out_port==ofproto.OFPP_LOCAL:
        #     match_udp = parser.OFPMatch(in_port=in_port,eth_type=ether_types.ETH_TYPE_IP,ip_proto=inet.IPPROTO_UDP,eth_src=src,eth_dst=dst)
        #     match_tcp = parser.OFPMatch(in_port=in_port,eth_type=ether_types.ETH_TYPE_IP,ip_proto=inet.IPPROTO_TCP,eth_src=src,eth_dst=dst)
        #     actions_udp=[parser.OFPActionOutput(ofproto.OFPP_LOCAL)]
        #     try:
        #         path = nx.shortest_path(self.net, src, dst, weight="unweighted")
        #         next = path[path.index(dpid) + 1]
        #         out_port = self.net[dpid][next]['port']
        #     except nx.NetworkXNoPath or KeyError:
        #         out_port = ofproto.OFPP_LOCAL
        #     actions_tcp = [parser.OFPActionOutput(out_port)]

        #     if msg.buffer_id != ofproto.OFP_NO_BUFFER:
        #         self.add_flow(datapath, match_udp, actions_udp,msg.buffer_id)
        #         self.add_flow(datapath, match_tcp, actions_tcp,msg.buffer_id)
        #         return 
        #     else:
        #         self.add_flow(datapath,match_udp,actions_udp)
        #         self.add_flow(datapath,match_tcp,actions_tcp)
        #     if pkt_tcp:
        #         actions=actions_tcp
        #     elif pkt_udp:
        #         actions=actions_udp
        
        elif out_port==ofproto.OFPP_FLOOD:#将包往除（入端口和最小生成树禁止端口）以外的端口洪泛
            actions=[]
            # sw_dpid=sdn_info_request.get_switch_dpid(user_name,topo_name) #{'s1': '0000267738265943'}
            # sw_int_dpid=l={k:int(v,16) for (k,v) in sw_dpid.items()}
            # dpid_sw={v:k for k,v in sw_dpid.items()}
            # sw_name=dpid_sw['{:016x}'.format(datapath.id)]
            # all_sw_ports=sdn_info_request.get_switch_port(user_name,topo_name)
            # ports=[int(item) for item in all_sw_ports[sw_name].values()]
            for port in datapath.ports:
                if(port != in_port and port not in self.forbid_port[datapath.id]):#in_port is int type
                    actions.append(parser.OFPActionOutput(port))
            
        data=None
        if(msg.buffer_id==ofproto.OFP_NO_BUFFER):
            data=msg.data
        out = datapath.ofproto_parser.OFPPacketOut(
            datapath=datapath, in_port=in_port,actions=actions, buffer_id=msg.buffer_id,data=data)
        datapath.send_msg(out)


    @set_ev_cls(ofp_event.EventOFPSwitchFeatures , CONFIG_DISPATCHER)
    def switch_features_handler(self , ev):
        # print("switch_features_handler is called")
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)]
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS , actions)]
        mod = datapath.ofproto_parser.OFPFlowMod(
        datapath=datapath, match=match, priority=0, instructions=inst)
        datapath.send_msg(mod)

    @set_ev_cls(event.EventSwitchEnter,[CONFIG_DISPATCHER,MAIN_DISPATCHER,HANDSHAKE_DISPATCHER])
    def get_topology_data(self, ev):
        # time.sleep(random.random() * 10)
        # self.count += 1
        # print(self.count)
        # time.sleep(6)
        # switch_list = self.get_the_switch(self.topology_api_app, None) 
        # for switch in switch_list:
        #     self.switch.add(switch.dp.id)
        # print("found node is ",len(self.switch))
        return
        # print('switch enter')
        # time.sleep(0.1)
        # switch_list = self.get_the_switch(self.topology_api_app, None) 
        # for switch in switch_list:
        #     self.switch.add(switch.dp.id)
        # print('now found sw node is ',len(self.switch))
        # self.net.add_nodes_from(list(self.switch))

    @set_ev_cls(ofp_event.EventOFPPortStatus, MAIN_DISPATCHER)
    def _port_status_handler(self, ev):
        """
            Handle the port status changed event.
        """
        msg = ev.msg
        reason = msg.reason
        port_no = msg.desc.port_no
        dpid = msg.datapath.id
        ofproto = msg.datapath.ofproto

        reason_dict = {ofproto.OFPPR_ADD: "added",
                       ofproto.OFPPR_DELETE: "deleted",
                       ofproto.OFPPR_MODIFY: "modified", }
        # print(dpid,':',len(msg.datapath.ports))
        if(reason==ofproto.OFPPR_DELETE and self.stopped==False):#拓扑结束
            print('forbid_edge',self.forbid_edge)
            print('forbid_port',self.forbid_port)
            for i in range(len(self.sfc_lst)):
                print('sfc_'+str(i)+':', self.sfc_lst[i].tsp,'->',self.sfc_lst[i].user,self.sfc_lst[i].link_lst)
                print('the path is:',self.path[i])
            self.stopped=True


            if reason in reason_dict:
                print("switch%d: port %s %s" % (dpid, reason_dict[reason], port_no))
            else:
                print("switch%d: Illeagal port state %s %s" % (port_no, reason))

    @set_ev_cls(ofp_event.EventOFPErrorMsg, [CONFIG_DISPATCHER, MAIN_DISPATCHER])
    def error_msg_handler(self, ev):
        msg = ev.msg
        self.logger.info('OFPErrorMsg received: type=0x%02x code=0x%02x ' 'message=%s',
                       msg.type, msg.code, utils.hex_array(msg.data))

    def send_barrier_request(self, datapath):
        ofp_parser = datapath.ofproto_parser
        req = ofp_parser.OFPBarrierRequest(datapath)
        datapath.send_msg(req)

    @set_ev_cls(ofp_event.EventOFPBarrierReply, MAIN_DISPATCHER)
    def barrier_reply_handler(self, ev):
        self.logger.info('OFPBarrierReply received')
    
    def get_switch_dpid(self,user, topo):
        data = {
            "user": user,
            "topo": topo
        }
        resp = requests.post(url=f"http://{MASTER_IP}:{MASTER_PORT}/switch_dpid/",json=data)
        # print(resp.json())
        return resp.json()


    def get_host_mac(self,user, topo):
        data = {
            "user": user,
            "topo": topo
        }
        resp = requests.post(url=f"http://{MASTER_IP}:{MASTER_PORT}/host_mac/",json=data)
        # print(resp.json())
        return resp.json()


    def get_switch_port(self,user, topo):
        data = {
            "user": user,
            "topo": topo
        }
        resp = requests.post(url=f"http://{MASTER_IP}:{MASTER_PORT}/link_port/",json=data)
        # print(resp.json())
        return resp.json()