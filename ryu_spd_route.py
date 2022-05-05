# 运行在RYU控制器，使用SPD算法对数据包进行路由并给交换机添加流表

from ryu.base import app_manager
from ryu import utils
from ryu.controller import mac_to_port
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
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
import multiprocessing
import requests

MASTER_IP = "10.1.1.123"
MASTER_PORT = "6000"


topo_name=setting.topo
user_name=setting.user_name


class RyuSpdRoute(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(RyuSpdRoute, self).__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.topology_api_app = self
        self.net=nx.DiGraph()
        self.switch=set()#dpid
        self.found_link=0
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
        self.sw_power= self.work_allocate(self.switch_adj_lst,setting.num_of_CPU)
        self.forbid_edge=self.kruskal(self.switch_adj_lst)#最小生成树中的禁止边
        self.forbid_port=dict()#self.forbid_port[dpid]={}
        
        self.no_of_nodes = 0
        self.no_of_links = sum([1 if j >0 else 0   for i in self.switch_adj_lst for j in i ])#表示所有交换机链路的数量
        self.sfc_lst=self.read_sfc_file()
        self.path=[0 for n in range(len(self.sfc_lst))]#在这里初始化sfc请求和相应的路
        self.stopped=False#拓扑结束
        self.topo_thread=hub.spawn(self._discover)

    def get_the_link(self,app, dpid=None):
        rep = app.send_the_request(event.EventLinkRequest(dpid))
        print('wait for link')
        return rep.links
    
    def get_the_switch(self,app, dpid=None):
        rep = app.send_the_request(event.EventSwitchRequest(dpid))
        print('wait for switch')
        return rep.switches if rep else []

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
            reply=req.reply_q.get(block=True,timeout=1)
            print('reply ok')
            return reply
        except queue.Empty:
            print('empty')
            return
    
    def _discover(self):
        '''用于发现拓扑'''
        while len(self.switch)!=len(self.switch_adj_lst):
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
            
            if len(self.net.edges()) != self.no_of_links+len(self.user_sw_dic)*2:
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
                        self.forbid_port[item[0]]=set()
                        self.monitor_port[item[0]]=set()
                    for link in self.net.edges():#存储需要监控的端口
                        self.monitor_port[link[0]].add(self.net._succ[link[0]][link[1]]['port'])
                        self.dst_dpid[(link[0],self.net._succ[link[0]][link[1]]['port'])]=link[1]
                        if (link[0],link[1]) in self.forbid_edge or (link[1],link[0]) in self.forbid_edge:
                            self.forbid_port[link[0]].add(self.net._succ[link[0]][link[1]]['port'])    
                    if sum([len(v[1]) for v in self.forbid_port.items()])==2*len(self.forbid_edge):
                        print('forbid port ok')
            else:
                print('kill the discover thread')
                hub.kill(self.topo_thread)
                
    def read_topo_file(self):
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
        forbid_edge=[(v[0]+1,v[1]+1) for v in forbid_edge]
        forbid_dpid=[]
        datapath_dpid=self.sw_dpid
        for item in forbid_edge:
            forbid_dpid.append(( int('0x'+datapath_dpid['s'+str(item[0])],16) , int('0x'+datapath_dpid['s'+str(item[1])],16)))
        print('forbid dpid is',forbid_dpid)
        print('forbid edge is ',forbid_edge)
        return forbid_dpid
    
    #产生是否有路的标定矩阵
    def matrix_pow(self,switch_adj_lst,start,end,queue):#to obtain A 的1次方到15次方,两点之间有15跳。
        def better_allpath(switch_adj_lst,s ,e,path=[]):
            def min_length(exist_length:set):#12是最大的sfc长度
                # exist_length.add(setting.sfc_size_range[1]+1)
                exist_length.add(16)
                sort_length=sorted(list(exist_length),reverse=True)
                min_value=sort_length[0]
                for i in range(len(exist_length))[1:]:
                    if sort_length[i]-sort_length[i-1]==-1:
                        min_value=sort_length[i]
                    else:
                        break
                return min_value-1

            global exist_length
            path=path+[s]
            if(s==e):
                exist_length.add(len(path))
                return [path]
            paths=[]
            for i in range(len(switch_adj_lst)):
                if(i not in path and switch_adj_lst[s][i]!=0 and len(path)<min_length(exist_length)):
                    ns=better_allpath(switch_adj_lst,i,e,path)
                    for n in ns:
                        paths.append(n)
            return paths
        
        def path_len_kind_lst(paths):
            length_set = set()
            for index in range(len(paths)):
                length_set.add(len(paths[index]) - 1)
            return list(length_set)
        
        node_num=len(switch_adj_lst)
        matrix=[]
        for index in range(16):
            matrix.append(list(np.zeros([node_num,node_num])))
        for i in range(node_num)[start:end+1]:
            print(i,'switch_adj_lst computing')
            for j in range(node_num)[i+1:]:
                # print(i,j)
                global exist_length
                exist_length=set()
                len_kind_lst=path_len_kind_lst(better_allpath(switch_adj_lst,i,j))
                for item in len_kind_lst:
                    if(item>setting.sfc_size_range[1]):
                        break
                    matrix[item][i][j]=1
                    matrix[item][j][i]=1

        queue.put(matrix)
        
    # 给多进程分配任务
    def work_allocate(self,switch_adj_lst,num_of_cpu):
        if len(switch_adj_lst)<20:
            q=multiprocessing.Queue()
            jobs=[]
            p=multiprocessing.Process(target=self.matrix_pow,args=(switch_adj_lst,0,len(switch_adj_lst)-2,q))
            jobs.append(p)
            p.start()
            matrix=q.get()
            for p in jobs:
                p.join()
            return list(matrix)
        else:
            all_work=[len(switch_adj_lst)-i-1 for i in range(len(switch_adj_lst))]
            # print('all_work is ',all_work)
            num_task_one_cpu=sum(all_work)/num_of_cpu
            # print('one cpu task is ',num_task_one_cpu)
            point=[len(switch_adj_lst)-1]
            sum_point=0
            for i in range(len(switch_adj_lst)):
                sum_point+=i
                if sum_point>num_task_one_cpu:
                    sum_point=0
                    point.append(len(switch_adj_lst)-i)
            
            point.append(0)
            point=sorted(list(set(point)))
            fragment=[]
            # print('point is ',point)
            for i in range(len(point)-1):
                if i==0:
                    fragment.append((point[i],point[i+1]))
                elif i==len(point)-2:
                    fragment.append((point[i]+1,point[i+1]-1))
                else:
                    fragment.append((point[i]+1,point[i+1]))
            print('fragment is ',fragment)
            q=multiprocessing.Queue()
            jobs=[]
            for i in range(len(fragment)):
                p=multiprocessing.Process(target=self.matrix_pow,args=(switch_adj_lst,fragment[i][0],fragment[i][1],q))
                jobs.append(p)
                p.start()
                print(i,'fragment computing')
            matrix=[]
            results=[q.get() for j in jobs]
            for index in range(16):
                matrix.append(list(np.zeros([len(switch_adj_lst),len(switch_adj_lst)])))
            matrix=np.array(matrix)
            for i in range(len(results)):
                matrix+=results[i]
            # print('获取完毕')
            # print('等待子进程结束')
            for p in jobs:
                p.join()
            return list(matrix)
        
    #产生随机的SFC的列表
    def req_event(self,sfc_num,user_num,user_sw_dic):
        SFC_req_lst=[]
        comp_demand_lst=[]
        link_demand_lst=[]
        user_tsp_lst=[]
        while(len(user_tsp_lst)<sfc_num):
            random.seed(time.time())
            user_tsp_lst.append((np.random.randint(0,user_num-1),np.random.randint(0,user_num-1)))
            index=len(user_tsp_lst)-1
            #保证: 不出现源、目的相同; 两个节点之间只有一个有向关系；不会出现同一交换机下的两个节点分别是SFC源，目的
            if(user_tsp_lst[index][0]==user_tsp_lst[index][1] or \
                user_sw_dic[user_tsp_lst[index][0]]==user_sw_dic[user_tsp_lst[index][1]] or \
                (user_tsp_lst[index][0],user_tsp_lst[index][1]) in user_tsp_lst[0:index] or \
                    (user_tsp_lst[index][1],user_tsp_lst[index][0]) in user_tsp_lst[0:index]):
                user_tsp_lst.pop()
        

        for index in range(sfc_num):
            random.seed(time.time())
            vnf_num=random.randint(2,4)#the number of vnf for one sfc
            comp_demand_lst.append(list(np.random.uniform(1,2,vnf_num)))
            # link_demand_lst.append(list(np.random.uniform(0.5, 2,vnf_num-1)))
            link_bw=random.uniform(0.5,2)
            link_demand_lst.append([link_bw for x in range(vnf_num-1)])#生成链路需求相同的sfc
        for index in range(sfc_num):
            SFC_req_lst.append(SFC(user_tsp_lst[index][1],user_tsp_lst[index][0],comp_demand_lst[index],link_demand_lst[index]))
        for i in range(sfc_num):
            print('sfc[%d]:user-%d,tsp-%d,com-%s,link-%s'%(i,SFC_req_lst[i].user,SFC_req_lst[i].tsp,\
                SFC_req_lst[i].comp_lst,SFC_req_lst[i].link_lst))
        return SFC_req_lst
    # #给数据包找路
    # def req_proc(self,sfc:SFC,sw_pow,user_sw_dic,switch_adj_lst,changed_link_lst,switch_comp_dic,changed_comp_dic):
    #     def has_path(sfc,user,tsp,path_len,sw_pow,user_sw_dic):
    #         exten=path_len
    #         while(sw_pow[exten][user_sw_dic[user]][user_sw_dic[tsp]]==0):
    #             # print('sw_pow[%d][%d][%d]=0' % (exten,user_sw_dic[user],user_sw_dic[tsp]))
    #             exten=exten+1
    #             if(exten==16):
    #                 return False
    #         if(exten>path_len):
    #             min_link=min(sfc.link_lst)
    #             min_link_index=sfc.link_lst.index(min_link)
    #             for i in range(exten-path_len):
    #                 sfc.comp_lst.insert(min_link_index+1,0)
    #                 sfc.link_lst.insert(min_link_index,min_link)

    #         # print('现在的comp是%s,现在的link是%s' % (sfc.comp_lst, sfc.link_lst))
    #         return True

    #     def SPD(sfc: SFC, start, vnf_lst: list, user_switch_dic, switch_adj_lst, changed_link_lst, switch_comp_dic,
    #             changed_comp_dic):#返回路径的交换机列表，不包括host
    #         #global flag
    #         server_adj_dic = {}  # 表示交换机-sf值的字典
    #         # print(vnf_lst)
    #         for i in range(len(switch_adj_lst)):  # i is the switch
    #             if (switch_adj_lst[start][i] != 0 and i not in vnf_lst):
    #                 server_adj_dic[i] = 0.5 * (switch_adj_lst[start][i] - changed_link_lst[start][i]) / \
    #                                     switch_adj_lst[start][i] \
    #                                     + 0.5 * (switch_comp_dic[i] - changed_comp_dic[i]) / switch_comp_dic[i]
    #         sorted_serv = sorted(server_adj_dic.items(), key=lambda item: item[1])
    #         # print(sorted_serv)
    #         for item in sorted_serv:
    #             # if (flag):
    #             #     break
    #             if (item[0] == user_switch_dic[sfc.user]):
    #                 if (len(vnf_lst) != len(sfc.comp_lst) - 1):
    #                     continue
    #                 else:
    #                     if (changed_link_lst[start][item[0]] >= sfc.link_lst[len(sfc.link_lst) - 1]):
    #                         vnf_lst=vnf_lst+[item[0]]
    #                         # print(vnf_lst)
    #                         flag = True
    #                         return vnf_lst

    #             else:
    #                 if (len(vnf_lst) == len(sfc.comp_lst) - 1):
    #                     continue
    #                 else:
    #                     if (len(vnf_lst) >= len(sfc.comp_lst)):
    #                         break
    #                     # print('index is %d, kength is %d' %(len(vnf_lst),len(sfc.comp_lst)))
    #                     # print('index is %d,length is %d' % (len(vnf_lst)-1,len(vnf_lst)))
    #                     if (changed_comp_dic[item[0]] >= sfc.comp_lst[len(vnf_lst)] and \
    #                             changed_link_lst[start][item[0]] >= sfc.link_lst[len(vnf_lst) - 1]):
    #                         # vnf_lst.append(item[0])
    #                         path_lst = SPD(sfc, item[0], vnf_lst+[item[0]], user_switch_dic, switch_adj_lst,changed_link_lst, switch_comp_dic, changed_comp_dic)

    #                         if (type(path_lst) == list and len(path_lst) == len(sfc.comp_lst) \
    #                                 and path_lst[0] == user_sw_dic[sfc.tsp] and path_lst[len(path_lst) - 1] == user_sw_dic[sfc.user]):
    #                             flag = True
    #                             return path_lst
            
        
    #     #global lock
    #     #global flag
    #     # print('tsp is %d,user is %d,compute_lst is %s,link_lst is %s' % (sfc.tsp,sfc.user,sfc.comp_lst,sfc.link_lst))
    #     if(has_path(sfc,sfc.user,sfc.tsp,len(sfc.link_lst),sw_pow,user_sw_dic)==False):
    #         print('no path,deploy failed')
    #         vnf_lst=None
    #     else:
    #         #if(lock.acquire()):
    #         if(True):
    #             vnf_lst=SPD(sfc,user_sw_dic[sfc.tsp],[user_sw_dic[sfc.tsp]],user_sw_dic,switch_adj_lst,changed_link_lst,switch_comp_dic,changed_comp_dic)
    #             if(vnf_lst==None):
    #                 print('vnf_lst=none,deploy failed')
    #             elif(len(vnf_lst)==len(sfc.comp_lst)):
    #                 print('have fangan')
    #                 for i in range(len(vnf_lst)):
    #                     changed_comp_dic[vnf_lst[i]]=changed_comp_dic[vnf_lst[i]] - sfc.comp_lst[i]
    #                     if(i==len(vnf_lst)-1):
    #                         continue
    #                     changed_link_lst[vnf_lst[i]][vnf_lst[i+1]]=changed_link_lst[vnf_lst[i]][vnf_lst[i+1]] - sfc.link_lst[i]
    #                     changed_link_lst[vnf_lst[i+1]][vnf_lst[i]]=changed_link_lst[vnf_lst[i]][vnf_lst[i+1]]
    #     return vnf_lst


    #给数据包找路
    def req_proc(self,sfc:SFC,sw_pow,user_sw_dic,switch_adj_lst,changed_link_lst,switch_comp_dic,changed_comp_dic):
        def has_path(sfc,user,tsp,hop_len,sw_pow,user_sw_dic):
            all_path_len=[]
            for i in range(16)[hop_len:]:
                if sw_pow[i][user_sw_dic[user]][user_sw_dic[tsp]] != 0:
                    all_path_len.append(i)

            return all_path_len
            # if(exten>path_len):
            #     min_link=min(sfc.link_lst)
            #     min_link_index=sfc.link_lst.index(min_link)
            #     for i in range(exten-path_len):
            #         sfc.comp_lst.insert(min_link_index+1,0)
            #         sfc.link_lst.insert(min_link_index,min_link)

            # print('现在的comp是%s,现在的link是%s' % (sfc.comp_lst, sfc.link_lst))
            

        def SPD(sfc: SFC, start, vnf_lst: list, user_switch_dic, switch_adj_lst, changed_link_lst, switch_comp_dic,
                changed_comp_dic):#返回路径的交换机列表，不包括host
            #global flag
            server_adj_dic = {}  # 表示交换机-sf值的字典
            # print(vnf_lst)
            for i in range(len(switch_adj_lst)):  # i is the switch
                if (switch_adj_lst[start][i] != 0 and i not in vnf_lst):
                    server_adj_dic[i] = 0.5 * (switch_adj_lst[start][i] - changed_link_lst[start][i]) / \
                                        switch_adj_lst[start][i] \
                                        + 0.5 * (switch_comp_dic[i] - changed_comp_dic[i]) / switch_comp_dic[i]
            sorted_serv = sorted(server_adj_dic.items(), key=lambda item: item[1])
            # print(sorted_serv)
            for item in sorted_serv:
                # if (flag):
                #     break
                if (item[0] == user_switch_dic[sfc.user]):
                    if (len(vnf_lst) != len(sfc.comp_lst) - 1):
                        continue
                    else:
                        if (changed_link_lst[start][item[0]] >= sfc.link_lst[len(sfc.link_lst) - 1]):
                            vnf_lst=vnf_lst+[item[0]]
                            # print(vnf_lst)
                            flag = True
                            return vnf_lst

                else:
                    if (len(vnf_lst) == len(sfc.comp_lst) - 1):
                        continue
                    else:
                        if (len(vnf_lst) >= len(sfc.comp_lst)):
                            break
                        # print('index is %d, kength is %d' %(len(vnf_lst),len(sfc.comp_lst)))
                        # print('index is %d,length is %d' % (len(vnf_lst)-1,len(vnf_lst)))
                        if (changed_comp_dic[item[0]] >= sfc.comp_lst[len(vnf_lst)] and \
                                changed_link_lst[start][item[0]] >= sfc.link_lst[len(vnf_lst) - 1]):
                            # vnf_lst.append(item[0])
                            path_lst = SPD(sfc, item[0], vnf_lst+[item[0]], user_switch_dic, switch_adj_lst,changed_link_lst, switch_comp_dic, changed_comp_dic)

                            if (type(path_lst) == list and len(path_lst) == len(sfc.comp_lst) \
                                    and path_lst[0] == user_sw_dic[sfc.tsp] and path_lst[len(path_lst) - 1] == user_sw_dic[sfc.user]):
                                flag = True
                                return path_lst
            

        # print('tsp is %d,user is %d,compute_lst is %s,link_lst is %s' % (sfc.tsp,sfc.user,sfc.comp_lst,sfc.link_lst))
        all_path_len=has_path(sfc,sfc.user,sfc.tsp,len(sfc.link_lst),sw_pow,user_sw_dic)
        print("sfc:",sfc.tsp,sfc.user,sfc.comp_lst,sfc.link_lst)
        print("all_path_len:",all_path_len)
        vnf_lst=None
        if len(all_path_len)==0:
            print('no path,deploy failed')
        else:
            all_path_len=sorted(all_path_len)
            
            for  length in all_path_len:
                print("length:",length)
                print('prior comp is %s,prior link is %s' % (sfc.comp_lst, sfc.link_lst))
                comp=copy.deepcopy(sfc.comp_lst)
                link=copy.deepcopy(sfc.link_lst)
                if(length>len(sfc.link_lst)):
                    min_link=min(link)
                    min_link_index=link.index(min_link)
                    for i in range(length-len(link)):
                        comp.insert(min_link_index+1,0)
                        link.insert(min_link_index,min_link)
                print(' now comp is %s,now link is %s' % (comp, link))
                # print('现在的comp是%s,现在的link是%s' % (sfc.comp_lst, sfc.link_lst))
                vnf_lst=SPD(SFC(sfc.tsp,sfc.user,comp,link),user_sw_dic[sfc.tsp],[user_sw_dic[sfc.tsp]],user_sw_dic,switch_adj_lst,changed_link_lst,switch_comp_dic,changed_comp_dic)
                print("vnf_lst:",vnf_lst)
                if vnf_lst!=None:
                    for i in range(len(vnf_lst)):
                        changed_comp_dic[vnf_lst[i]]=changed_comp_dic[vnf_lst[i]] - comp[i]
                        if i==len(vnf_lst)-1:
                            continue
                        changed_link_lst[vnf_lst[i]][vnf_lst[i+1]]=changed_link_lst[vnf_lst[i]][vnf_lst[i+1]] - link[i]
                        changed_link_lst[vnf_lst[i+1]][vnf_lst[i]]=changed_link_lst[vnf_lst[i]][vnf_lst[i+1]]
                    
                    return vnf_lst
        return vnf_lst

    
    # Handy function that lists all attributes in the given object
    def ls(self,obj):
        print("\n".join([x for x in dir(obj) if x[0] != "_"]))
    #将int值也就是矩阵的索引值转换为DPID
    def trans_int_dpid(self,vnf_lst:list,src:str,dst:str):#s[1]:'0000000000000001'
        datapath_dpid=self.sw_dpid
        # print('vnf_lst is ',vnf_lst)
        for i in range(len(vnf_lst)):
            vnf_lst[i]=int('0x'+datapath_dpid['s'+str(vnf_lst[i]+1)],16)
            
        vnf_lst.append(dst)
        vnf_lst.insert(0,src)
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
                priority=1,instructions=inst)
        datapath.send_msg(mod)

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
        if src not in self.net:
            self.net.add_node(src)
            self.net.add_edge(dpid,src,port=in_port,weight=0)
            self.net.add_edge(src,dpid,weight=0)
        out_port=None
        if dst in self.net:
            # print('dst in:',src,dst,dpid)

            # if(pkt_icmp or pkt_tcp or eth.ethertype==ether_types.ETH_TYPE_ARP):
            if(pkt_icmp or eth.ethertype==ether_types.ETH_TYPE_ARP):
                try:
                    path = nx.shortest_path(self.net, src, dst, weight="unweighted")
                    # print ("dpid=", dpid)
                    #print ("length=", nx.shortest_path_length(self.net, src, dst, weight="weight"))
                    next = path[path.index(dpid) + 1]
                    out_port = self.net[dpid][next]['port']
                    # print('0-src and dst is: ',src,dst)
                    # print('path is ',path)
                    # print('dpid and next is ',dpid,next)
                    shortest=True
                except nx.NetworkXNoPath or ValueError or KeyError:
                    # out_port = ofproto.OFPP_FLOOD
                    print('path is ',path)
                    print('all switch is ',self.switch)
                    print('nopath or error--flood')
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
                        vnf_lst=self.req_proc(self.sfc_lst[pk_index],self.sw_power,self.user_sw_dic,\
                        self.switch_adj_lst,self.changed_link,self.switch_comp_dic,self.changed_comp)
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
        
        
        
        
        
        # # install a flow to avoid packet_in next time
        # if out_port != ofproto.OFPP_FLOOD and shortest==False and out_port !=ofproto.OFPP_LOCAL:
        #     # match = parser.OFPMatch(in_port=in_port,eth_src=src,eth_dst=dst)
        #     match_udp = parser.OFPMatch(in_port=in_port,eth_type=ether_types.ETH_TYPE_IP,ip_proto=inet.IPPROTO_UDP,eth_src=src,eth_dst=dst)
        #     match_tcp = parser.OFPMatch(in_port=in_port,eth_type=ether_types.ETH_TYPE_IP,ip_proto=inet.IPPROTO_TCP,eth_src=src,eth_dst=dst)
            
        #     # print('inport:',in_port)
        #     if msg.buffer_id != ofproto.OFP_NO_BUFFER:
        #         self.add_flow(datapath, match_udp, actions,msg.buffer_id)
        #         self.add_flow(datapath, match_tcp, actions,msg.buffer_id)
        #         # print('1switch -id:', dpid)
        #         return 
        #     else:
        #         # match = parser.OFPMatch(in_port=in_port,eth_type=ether_types.ETH_TYPE_IP,ip_proto=inet.IPPROTO_UDP,ipv4_src=ip_src,ipv4_dst=ip_dst,eth_src=src,eth_dst=dst)
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
        #     actions_udp=actions_udp=[parser.OFPActionOutput(ofproto.OFPP_LOCAL)]
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
            # print('sw-dpid is ',sw_dpid)
            # sw_int_dpid=l={k:int(v,16) for (k,v) in sw_dpid.items()}
            # print('sw_int_dpid is ',sw_int_dpid)
            # dpid_sw={v:k for k,v in sw_dpid.items()}
            # print('dpid-sw is',dpid_sw)
            # sw_name=dpid_sw['{:016x}'.format(datapath.id)]
            # print('sw_name is',sw_name)
            # all_sw_ports=sdn_info_request.get_switch_port(user_name,topo_name)
            # print('all_sw_ports is ',all_sw_ports)
            # ports=[int(item) for item in all_sw_ports[sw_name].values()]
            # print('ports is ',ports)
            # print('forbid port is ',self.forbid_port[datapath.id])
            for port in datapath.ports:
                if(port != in_port and port not in self.forbid_port[datapath.id]):
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

    @set_ev_cls(event.EventSwitchEnter,[CONFIG_DISPATCHER,MAIN_DISPATCHER])
    def get_topology_data(self, ev):
        return 
        # print('switch enter')
        # time.sleep(2)
        # switch_list = get_switch(self.topology_api_app, None)  
        # switches=[switch.dp.id for switch in switch_list]
        # self.switch=switches
        # self.net.add_nodes_from(switches)

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