# #交换机拓扑及链路带宽的矩阵描述


num_of_switch=24
topo='shy_test_'+str(num_of_switch)+'_sw'#拓扑的拓扑名
print(topo)
num_of_user=num_of_switch
num_of_sfc=30#SFC请求条数
stage2_time=30#600#iperf UDP流的时间,每条0.15秒


path=topo
base_port = 5001
degree=2#每个交换机的度数,建议 <=3，否则计算matrix_pow时间会很长
probability=0.2#degree/num_of_switch#拓扑中两点连接边的概率
min_degree=2
ave_degree=4

sw_conpute_range=(150,150)
sw_link_bw_range=(10,10)
num_of_CPU=6  #即子进程数量
user_name='sw'#拓扑的用户名
name='my_test'       


sfc_size_range=(2,4)
sfc_comp_range=(1,2)
sfc_link_range=(2,4)




stage1_time=1#iperf TCP流的时间间隔

monitor_period=1#请求网络负载的时间间隔
monitor_sleep_time=5#用于等待发现链路
monitor_time=num_of_sfc*(stage1_time)+stage2_time#监控总时间

class SFC(object):
    def __init__(self,tsp:int,user:int,comp_lst:list,link_lst:list):  #the requestor,the source server ,the compute resource demand,the link resource demand
        self.tsp=tsp# path start
        self.user=user#prth end
        self.comp_lst=comp_lst
        self.link_lst=link_lst
        # self.port = port
    def show(self):
        return 'tsp is %d,user is %d,port is %d, com_lst is %s,link_lst is %s'%(self.tsp,self.user, self.port,str(self.comp_lst),str(self.link_lst))