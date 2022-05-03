# 根据setting.py中的网络拓扑矩阵等参数，生成用于部署拓扑的topo_post.json文件、用于主机iperf的sfc_post.json文件
# 用于描述SFC请求的sfc_lst.txt文件
import copy
import json
import setting
import random
from re import T
import numpy as np
import time
import pickle
from setting import SFC
import os
import shutil



nowpath=os.getcwd()
folder=os.path.exists(nowpath+'\\'+setting.path)
if not folder:
    os.mkdir(nowpath+'\\'+setting.path)
    
os.chdir(nowpath+'\\'+setting.path)
shutil.copyfile(nowpath+'\\setting.py', nowpath+'\\'+setting.path+'\\setting.py')

def read_topo_file():
        with open('topo_info.txt','rb') as f:
            topo_info=pickle.load(f)
        # print('topo_info:',topo_info)
        switch_adj_lst=topo_info['switch_adj_lst']
        user_sw_dic=topo_info['user_sw_dic']
        return switch_adj_lst,user_sw_dic


switch_adj_lst,user_sw_dic = read_topo_file()

user_name = setting.user_name
topo = setting.topo
name = setting.name
num_of_sfc = setting.num_of_sfc
sfc_size_range=setting.sfc_size_range
sfc_comp_range=setting.sfc_comp_range
sfc_link_range=setting.sfc_link_range
stage1_time = setting.stage1_time
stage2_time = setting.stage2_time




def req_event(sfc_num, user_num, user_sw_dic,sfc_size_range,sfc_comp_range,sfc_link_range):
    # req_port = setting.base_port
    SFC_req_lst = []
    comp_demand_lst = []
    link_demand_lst = []
    user_tsp_lst = []
    print(7)
    while(len(user_tsp_lst) < sfc_num):
        print(len(user_tsp_lst), sfc_num)
        # random.seed(time.time())
        user_tsp_lst.append((np.random.randint(0, user_num-1),
                            np.random.randint(0, user_num-1)))
    #     index = len(user_tsp_lst)-1
    #     # 保证: 不出现源、目的相同; 两个节点之间只有一个有向关系；不会出现同一交换机下的两个节点分别是SFC源，目的
    #     if(user_tsp_lst[index][0] == user_tsp_lst[index][1] or
    #             user_sw_dic[user_tsp_lst[index][0]] == user_sw_dic[user_tsp_lst[index][1]] or
    #             (user_tsp_lst[index][0], user_tsp_lst[index][1]) in user_tsp_lst[0:index] or
    #        (user_tsp_lst[index][1], user_tsp_lst[index][0]) in user_tsp_lst[0:index]):
    #         user_tsp_lst.pop()
    # print(8)
    for index in range(sfc_num):
        vnf_num = random.randint(sfc_size_range[0], sfc_size_range[1])  # the number of vnf for one sfc
        comp_demand_lst.append(list(np.random.uniform(sfc_comp_range[0], sfc_comp_range[1], vnf_num)))
        link_bw = random.uniform(sfc_link_range[0], sfc_link_range[1])
        link_demand_lst.append(
            [link_bw for x in range(vnf_num-1)])  # 生成链路需求相同的sfc
    for index in range(sfc_num):
        SFC_req_lst.append(SFC(
            user_tsp_lst[index][1], user_tsp_lst[index][0], comp_demand_lst[index], link_demand_lst[index]))
        # req_port = req_port + 1
        
    for i in range(sfc_num):
        print('sfc[%d]:tsp-%d,user-%d,com-%s,link-%s' % (i,SFC_req_lst[i].tsp, SFC_req_lst[i].user,
                                                        SFC_req_lst[i].comp_lst, SFC_req_lst[i].link_lst))
    return SFC_req_lst

def topo_post(switch_adj_lst, user_sw_dic, user_name, topo, name):
    d = dict()
    d['user'] = user_name
    d['topo'] = topo
    d['name'] = name
    d['networks'] = dict()
    d['networks']['controllers'] = {
        "ctr1": {
            "config": {
                "port": 6633

            },
            "image_name": "controller/ryu",
            "name": "ctr1",
            "subtype": "ryu",
            "type": "controller",
            "x": 162,
            "y": 46.39999961853027
        }
    }
    d['networks']['hosts'] = dict()
    d['networks']['links'] = dict()
    d['networks']['routers'] = dict()
    d['networks']['switches'] = dict()

    host = {
        "gateway": "",
        "image_name": "host/ubuntu",
        "interfaces": [{
            "ip": "192.168.1.2",
            "name": "h1s1",
            "netmask": "255.255.255.0"
        }],
        "name": "h1",
        "subtype": "ubuntu",
        "type": "host",
        "x": 115,
        "y": 128
    }
    link = {
        "config": {
            "source": {
                "bw_kbit": "29000",
                "correlation": "",
                "delay_distribution": "normal",
                "delay_us": "0",
                "jitter_us": "",
                "loss_rate": "",
                "queue_size_byte": "1000"
            },
            "target": {
                "bw_kbit": "29000",
                "correlation": "",
                "delay_distribution": "normal",
                "delay_us": "0",
                "jitter_us": "",
                "loss_rate": "",
                "queue_size_byte": "1000"
            }
        },
        "name": "l10",
        "source": "s5",
        "sourceIP": "",
        "sourceType": "switch",
        "target": "s6",
        "targetIP": "",
        "targetType": "switch"
    }
    switch = {
        "config": {
            "controllers": ["ctr1"],
            "stp": False
        },
        "image_name": "switch/ovs",
        "name": "s1",
        "subtype": "ovs",
        "type": "switch",
        "x": 252,
        "y": 140
    }

    for i in range(len(switch_adj_lst)):
        sw = copy.deepcopy(switch)
        sw['name'] = 's'+str(i+1)
        d['networks']['switches']['s'+str(i+1)] = sw
    # print(d['networks']['switches'])

    for i in range(len(user_sw_dic)):
        h = copy.deepcopy(host)
        h['interfaces'][0]['ip'] = '192.168.1.'+str(i+1)
        h['interfaces'][0]['name'] = 'h'+str(i+1)+'s'+str(user_sw_dic[i]+1)
        h['name'] = 'h'+str(i+1)
        d['networks']['hosts']['h'+str(i+1)] = h
    # print(d['networks']['hosts'])

    for i in range(len(switch_adj_lst)):
        for j in range(len(switch_adj_lst)):
            if i < j and switch_adj_lst[i][j] != 0:
                l = copy.deepcopy(link)
                l['config']['source']['bw_kbit'] = str(
                    switch_adj_lst[i][j]*1000)
                l['config']['target']['bw_kbit'] = str(
                    switch_adj_lst[i][j]*1000)
                l['name'] = 'l'+str(len(d['networks']['links'])+1)
                l['source'] = 's'+str(i+1)
                l['target'] = 's'+str(j+1)
                d['networks']['links']['l' +
                                       str(len(d['networks']['links'])+1)] = l

    for i in range(len(user_sw_dic)):
        l = copy.deepcopy(link)
        l['config']['source']['bw_kbit'] = str(1000*1000)
        l['config']['target']['bw_kbit'] = str(1000*1000)
        l['name'] = 'l'+str(len(d['networks']['links'])+1)
        l['source'] = 'h'+str(i+1)
        l['sourceIP'] = '192.168.1.'+str(i+1)+'/24'
        l['sourceType'] = 'host'
        l['target'] = 's'+str(user_sw_dic[i]+1)
        d['networks']['links']['l'+str(len(d['networks']['links'])+1)] = l
    # print(d['networks']['links'])
    j = json.dumps(d)
    return j

def sfc_post(user_name, topo, stage1_time, stage2_time, sfc_lst):
    # print(1)
    sfc_json = dict()
    sfc_json['user'] = user_name
    sfc_json['topo'] = topo
    sfc_json['stage1_time'] = str(stage1_time)
    sfc_json['stage2_time'] = str(stage2_time)
        
    sfc_dic_lst =[]
    
    # one_sfc={"src":"h1","dst":"h4","tcp_bw":"4"}
    
    for i in range(len(sfc_lst)):
        # sfc_dic[i] = [sfc_lst[i].tsp, sfc_lst[i].user,
        #               sfc_lst[i].comp_lst, sfc_lst[i].link_lst]
        one_sfc={"src":"h"+str(sfc_lst[i].tsp+1),"dst":"h"+str(sfc_lst[i].user+1),"tcp_bw":str(sfc_lst[i].link_lst[0])}
        sfc_dic_lst.append(one_sfc)
        # print('kjnf')
        # print(one_sfc)
        
    sfc_json['sfc'] = sfc_dic_lst
    # print(3)
    j = json.dumps(sfc_json)
    return j


with open('topo_post.json', 'w') as f:
    j = topo_post(switch_adj_lst, user_sw_dic, user_name, topo, name)
    f.write(j)

sfc_lst = req_event(num_of_sfc, len(user_sw_dic), user_sw_dic,sfc_size_range,sfc_comp_range,sfc_link_range)
with open('sfc_lst.txt', 'wb') as f:
    pickle.dump(sfc_lst, f)

with open('sfc_post.json', 'w') as f:
    j = sfc_post(user_name, topo, stage1_time, stage2_time, sfc_lst)
    f.write(j)


print('ok')
print('sw num is ',len(switch_adj_lst))
print('sw link is ',sum([1 if j >0 else 0   for i in switch_adj_lst for j in i ]))
print('all link is ',sum([1 if j >0 else 0   for i in switch_adj_lst for j in i ])+2*len(user_sw_dic))