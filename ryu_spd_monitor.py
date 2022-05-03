# 运行在RYU控制器，继承于ryu_spd_route.RyuSpdRoute,使用SPD算法对数据包进行路由并给交换机添加流表，
# 定时记录网络负载，将网络负载记录写入spd_net_load.txt文件，并画出图
from operator import attrgetter
from ryu.ofproto import ofproto_v1_3
import ryu_spd_route
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.topology.api import get_switch, get_link
from ryu.controller.handler import set_ev_cls
from ryu.lib import hub
import matplotlib.pyplot as plt
import pickle
import setting
topo_name=setting.topo
user_name=setting.user_name


monitor_period=setting.monitor_period
monitor_time=setting.monitor_time


class MyMonitor(ryu_spd_route.RyuSpdRoute):
    def __init__(self,*args,**kwargs):
        super(MyMonitor,self).__init__(*args,**kwargs)
        self.datapaths={}
        self.port_stats={}
        self.port_speed={}
        self.net_load=[]
        self.stats={}
        self.port_features = {}
        self.monitor_thread=hub.spawn(self._monitor)
        self.has_plot=False#画图完成


    @set_ev_cls(ofp_event.EventOFPStateChange,
                [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change_handler(self, ev):
        '''记录交换机信息'''
        datapath = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            if datapath.id not in self.datapaths:
                self.logger.debug('register datapath: %016x', datapath.id)
                self.datapaths[datapath.id] = datapath
        elif ev.state == DEAD_DISPATCHER:
            if datapath.id in self.datapaths:
                self.logger.debug('unregister datapath: %016x', datapath.id)
                del self.datapaths[datapath.id]


    def _monitor(self):
        '''监控的主程序'''
        while True:
            if len(self.net.edges()) != self.no_of_links+len(self.user_sw_dic)*2:
                hub.sleep(setting.monitor_sleep_time)
                continue
            
            if len(self.net_load)>=monitor_time/monitor_period:
                # print('forbid_edge',self.forbid_edge)
                # print('forbid_port',self.forbid_port)
                # for i in range(len(self.sfc_lst)):
                #     print('sfc_'+str(i)+':', self.sfc_lst[i].tsp,'->',self.sfc_lst[i].user,self.sfc_lst[i].link_lst)
                #     print('the path is:',self.path[i])
                #画图
                # self.net_load=[0 if v>1 else v for v in self.net_load]
                self.net_load=self.net_load[int((setting.stage1_time*(setting.num_of_sfc-1)) / monitor_period):]
                x=[0]
                for i in range(len(self.net_load)-1):
                    x.append(x[i]+monitor_period)
                plt.figure(figsize=(8,4))
                plt.plot(x,self.net_load,"b--",linewidth=1)
                plt.xlabel("time(s)")
                plt.ylabel("mean_link_load")
                plt.title("SPD network load")
                plt.savefig('load_of_SPD.png')
                # plt.show()
                with open('spd_net_load.txt','wb') as f:
                    pickle.dump(self.net_load,f)
                # for i in range(len(self.sfc_lst)):
                #     print('sfc_'+str(i)+':', self.sfc_lst[i].tsp,'->',self.sfc_lst[i].user,self.sfc_lst[i].link_lst)
                #     print('the path is:',self.path[i])
                print('net_load is ',self.net_load)
                success_rate=sum([0 if i ==0 else 1   for i in self.path]) / len(self.path)
                print("----------------------------------Average Resource Utilization-----------------------------------\n")
                print("topo name\tSFC length\tSFC number\tSFC BandWidth\tOSFC Resource\tOSFC Success Rate\n")
                print(setting.topo,"\t", setting.sfc_size_range,"\t   ", setting.num_of_sfc,"\t\t",\
                                    setting.sfc_link_range, "\t", "  %.2f%%"%(sum(self.net_load[-10:]) / 10), "\t", "   %.2f%%"%success_rate)
                print("\n-------------------------------------------------------------------------------------------------")
                
                # print("SFC deploy success rate is %.4f"%success_rate)
                break
            
            self.stats['port']={}
            for dp in self.datapaths.values():
                self.port_features.setdefault(dp.id, {})
                self._request_stats(dp)
                # print('send request')
            hub.sleep(monitor_period)
            if(self.stats['port']):
                # self.show_stat('port')
                self.my_show_stat('port')
            else:
                print("netload-",0)
                self.net_load.append(0)

    def _request_stats(self, datapath):
        '''向交换机发送请求消息'''
        self.logger.debug('send stats request: %016x', datapath.id)
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        # req = parser.OFPFlowStatsRequest(datapath)
        # datapath.send_msg(req)

        # req = parser.OFPPortDescStatsRequest(datapath, 0)
        # datapath.send_msg(req)

        req = parser.OFPPortStatsRequest(datapath, 0, ofproto.OFPP_ANY)
        datapath.send_msg(req)
    
    def _save_stats(self,_dict,key,value,length):
        if(key not in _dict):
            _dict[key]=[]
        _dict[key].append(value)

        if(len(_dict[key])>length):
            _dict[key].pop(0)

    def _get_speed(self,now,pre,period):
        if(period):
            return (now-pre)/period
        else:
            return 0

    def _get_time(self,sec,nsec):
        return sec+nsec/(10**9)
    
    def _get_period(self,n_sec,n_nsec,p_sec,p_nsec):
        return self._get_time(n_sec,n_nsec) - self._get_time(p_sec,p_nsec)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats_reply_handler(self, ev):
        '''存储端口的统计信息，计算端口速率并保存'''
        body = ev.msg.body
        dpid=ev.msg.datapath.id
        self.stats['port'][dpid]=body

        for stat in sorted(body,key=attrgetter('port_no')):
            port_no=stat.port_no
            if(port_no != ofproto_v1_3.OFPP_LOCAL):
                key=(dpid,port_no)
                value=(stat.tx_bytes , stat.rx_bytes , stat.rx_errors,
                        stat.duration_sec , stat.duration_nsec)
                self._save_stats(self.port_stats , key , value , 5)

                #计算端口速率
                pre=0
                period=monitor_period
                tmp=self.port_stats[key]
                if(len(tmp)>1):
                    # pre=tmp[-2][0]+tmp[-2][1]
                    pre=tmp[-2][0]
                    period=self._get_period(tmp[-1][3] , tmp[-1][4],
                                            tmp[-2][3] , tmp[-2][4])
                # speed=self._get_speed(self.port_stats[key][-1][0] + self.port_stats[-1][1] , pre , period)
                # speed=self._get_speed(tmp[-1][0] + tmp[-1][1] , pre , period)
                speed=self._get_speed(tmp[-1][0] , pre , period)#计算发送的包速率
                self._save_stats(self.port_speed,key,speed,5)

    def my_show_stat(self,type):
        '''根据type值展示相应的统计信息'''
        bodys=self.stats[type]
        net_load=0.0
        if(type=='port'):
            # print('src_datapath          dst_datapath           port    '
            #       'tx-bytes port-speed(Mb/s) '' link-width(Mb/s)  load-rate')
            # print('----------------      ----------------       --------'
            #       '-------- ---------------- '' ----------------  -------- ')
            # format = '%016x %016x %8x %8d %8.3f %16d %8.3f '
            for dpid in bodys.keys():
                for stat in sorted(bodys[dpid], key=attrgetter('port_no')):
                    if stat.port_no != ofproto_v1_3.OFPP_LOCAL and (stat.port_no in self.monitor_port[dpid]):
                        # print('%016x %8x %8d %8d %8d %8d %8d %8d %16.1f' % (
                        #     dpid, stat.port_no,
                        #     stat.rx_packets, stat.rx_bytes, stat.rx_errors,
                        #     stat.tx_packets, stat.tx_bytes, stat.tx_errors,
                        #     8*abs(self.port_speed[(dpid, stat.port_n
                        # o)][-1])/(10**6)))
                        dst_dpid=self.dst_dpid[(dpid,stat.port_no)]
                        sw_dpid=self.sw_dpid
                        dpid_sw={v:k for k,v in sw_dpid.items()}
                        s_sw_name=dpid_sw['{:016x}'.format(dpid)]
                        e_sw_name=dpid_sw['{:016x}'.format(dst_dpid)]
                        
                        link_bw=self.switch_adj_lst[int(s_sw_name[1:])-1][int(e_sw_name[1:])-1]
                        port_speed=8.0*(self.port_speed[(dpid, stat.port_no)][-1])/(10**6)
                        port_load = port_speed / link_bw
                        net_load += port_load 

                        # print(format %(dpid , dst_dpid , stat.port_no , stat.tx_bytes,
                        #         port_speed , link_bw , port_load))
            self.net_load.append(net_load / self.no_of_links)
            # print("net_load:",net_load / self.no_of_links)
                        # print( self.port_features[dpid][stat.port_no])
            #print(self.switch_lst)
            #print(links_lst)

            # load=0
            # for item in links_lst:
            #     try:
            #         load_rate_1=8*abs(self.port_speed[(item.src.dpid,item.src.port_no)][-1]) / (10**6) / self.switch_lst[item.src.dpid-1][item.dst.dpid-1]
            #         print(item.src.dpid,item.dst.dpid,'load1:,',load_rate_1)
            #         print('link width:',self.switch_lst[item.src.dpid-1][item.dst.dpid-1],'speed:',8*abs(self.port_speed[(item.src.dpid,item.src.port_no)][-1]) / (10**6))
            #         load_rate_2=8*abs(self.port_speed[(item.dst.dpid,item.dst.port_no)][-1]) / (10**6) / self.switch_lst[item.dst.dpid-1][item.src.dpid-1]
            #         print('load2:,',load_rate_2)
            #         load=load+(load_rate_1+load_rate_2) / 2

            #         stat=bodys[item.src.dpid][item.src.port_no]
            #         print(format % (
            #                 item.src.dpid, item.dst.dpid,stat.port_no,
            #                 stat.rx_packets, stat.rx_bytes, stat.rx_errors,
            #                 stat.tx_packets, stat.tx_bytes, stat.tx_errors,
            #                 8*abs(self.port_speed[(item.src.dpid,item.src.port_no)][-1]) / (10**6),
            #                 self.switch_lst[item.src.dpid-1][item.dst.dpid-1],load_rate_1))
            #         stat=bodys[item.dst.dpid][item.dst.port_no]
            #         print(format % (
            #                 item.dst.dpid, item.src.dpid,stat.port_no,
            #                 stat.rx_packets, stat.rx_bytes, stat.rx_errors,
            #                 stat.tx_packets, stat.tx_bytes, stat.tx_errors,
            #                 8*abs(self.port_speed[(item.dst.dpid,item.dst.port_no)][-1]) / (10**6),
            #                 self.switch_lst[item.dst.dpid-1][item.src.dpid-1],load_rate_2))
            #     except KeyError:
            #         continue
            # load=load / len(links_lst)
            # print('load:',load)

            
            # print('\n')

   
    @set_ev_cls(ofp_event.EventOFPPortDescStatsReply, MAIN_DISPATCHER)
    def port_desc_stats_reply_handler(self, ev):
        """
            Save port description info.
        """
        msg = ev.msg
        dpid = msg.datapath.id
        ofproto = msg.datapath.ofproto

        # config_dict = {ofproto.OFPPC_PORT_DOWN: "Down",
        #                ofproto.OFPPC_NO_RECV: "No Recv",
        #                ofproto.OFPPC_NO_FWD: "No Farward",
        #                ofproto.OFPPC_NO_PACKET_IN: "No Packet-in"}

        # state_dict = {ofproto.OFPPS_LINK_DOWN: "Down",
        #               ofproto.OFPPS_BLOCKED: "Blocked",
        #               ofproto.OFPPS_LIVE: "Live"}

        ports = []
        for p in ev.msg.body:
            # ports.append('port_no=%d hw_addr=%s name=%s config=0x%08x '
            #              'state=0x%08x curr=0x%08x advertised=0x%08x '
            #              'supported=0x%08x peer=0x%08x curr_speed=%d '
            #              'max_speed=%d' %
            #              (p.port_no, p.hw_addr,
            #               p.name, p.config,
            #               p.state, p.curr, p.advertised,
            #               p.supported, p.peer, p.curr_speed,
            #               p.max_speed))

            # if p.config in config_dict:
            #     config = config_dict[p.config]
            # else:
            #     config = "up"

            # if p.state in state_dict:
            #     state = state_dict[p.state]
            # else:
            #     state = "up"

            # port_feature = (config, state, p.curr_speed)
            # self.port_features[dpid][p.port_no] = port_feature
            self.port_features[dpid][p.port_no]=('port_no=%d hw_addr=%s name=%s config=0x%08x '
                         'state=0x%08x curr=0x%08x advertised=0x%08x '
                         'supported=0x%08x peer=0x%08x curr_speed=%d '
                         'max_speed=%d' %
                         (p.port_no, p.hw_addr,
                          p.name, p.config,
                          p.state, p.curr, p.advertised,
                          p.supported, p.peer, p.curr_speed,
                          p.max_speed))


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

        if(reason==ofproto.OFPPR_DELETE and self.has_plot==False):#拓扑结束
            self.has_plot=True
            #输出相关信息，包括禁止端口、sfc对应的路径
            print('forbid_edge',self.forbid_edge)
            print('forbid_port',self.forbid_port)
            for i in range(len(self.sfc_lst)):
                print('sfc_'+str(i)+':', self.sfc_lst[i].tsp,'->',self.sfc_lst[i].user,self.sfc_lst[i].link_lst)
                print('the path is:',self.path[i])
            #画图
            x=[item + monitor_period  for item in range(len(self.net_load))]
            plt.figure(figsize=(8,4))
            plt.plot(x,self.net_load,"b--",linewidth=1)
            plt.xlabel("time(s)")
            plt.ylabel("speed / bandwidth")
            plt.title("network load")
            plt.savefig('load of SPD.png')
            #plt.show()

            

        if reason in reason_dict:
            print("switch%d: port %s %s" % (dpid, reason_dict[reason], port_no))
        else:
            print("switch%d: Illeagal port state %s %s" % (port_no, reason))



        # self.logger.info('datapath         port     '
        #                  'rx-pkts  rx-bytes rx-error '
        #                  'tx-pkts  tx-bytes tx-error')
        # self.logger.info('---------------- -------- '
        #                  '-------- -------- -------- '
        #                  '-------- -------- --------')
        # for stat in sorted(body, key=attrgetter('port_no')):
        #     self.logger.info('%016x %8x %8d %8d %8d %8d %8d %8d',
        #                      ev.msg.datapath.id, stat.port_no,
        #                      stat.rx_packets, stat.rx_bytes, stat.rx_errors,
        #                      stat.tx_packets, stat.tx_bytes, stat.tx_errors)

