
#生成拓扑：两个交换机之间以probability的概率连接，主机连接交换机
import setting
import random
import numpy as np
import pickle
import os
import re

num_of_switch=setting.num_of_switch
sw_conpute_range=setting.sw_conpute_range
sw_link_bw_range=setting.sw_link_bw_range
num_of_user=setting.num_of_user
probability=setting.probability

# def topo_generator(num_of_switch,sw_compute_range,sw_link_bw_range,num_of_user,probability):
#     switch_adj_lst=[[0 for i in range(num_of_switch)] for j in range(num_of_switch)]
#     for i in range(num_of_switch):
#         for j in range(num_of_switch)[i+1:]:
#             if j-i==1:
#                 switch_adj_lst[i][j]=random.randint(sw_link_bw_range[0], sw_link_bw_range[1])
#                 switch_adj_lst[j][i]=switch_adj_lst[i][j]
#             else:
#                 switch_adj_lst[i][j]=(1 if random.random()<=probability else 0)*random.randint(sw_link_bw_range[0], sw_link_bw_range[1])
#                 switch_adj_lst[j][i]=switch_adj_lst[i][j]
#     # print(switch_adj_lst)
#     user_sw_dic=dict()
#     for i in range(num_of_user):
#         if(i<num_of_switch):
#             user_sw_dic[i]=i
#         else:
#             user_sw_dic[i]=int(random.random()*num_of_switch-0.1)
#     # print(user_sw_dic)
#     switch_comp_dic=dict()
#     for i in range(num_of_switch):
#         switch_comp_dic[i]=random.randint(sw_compute_range[0], sw_compute_range[1])
#     # print(switch_comp_dic)
    
#     topo_matrix=dict()
#     topo_matrix['switch_adj_lst']=switch_adj_lst
#     topo_matrix['user_sw_dic']=user_sw_dic
#     topo_matrix['switch_comp_dic']=switch_comp_dic
#     print('topo_info:',topo_matrix)
#     return topo_matrix

def topo_generator(num_of_switch,sw_compute_range,sw_link_bw_range,num_of_user,probability):
    def detect(switch_adj_lst):
        def find_set(node,set_node,node_num):
            for i in range(node_num):
                if(node in set_node[i]):
                    return i
        def union(set1,set2,set_node):
            set_node[set1]=list(set(set_node[set1]+set_node[set2]))
            if(set1==set2):
                return
            set_node[set2]=[]

        for i in range(len(switch_adj_lst)):
            for j in range(len(switch_adj_lst))[i+1:]:
                if switch_adj_lst[i][j]!=switch_adj_lst[j][i]:
                    print('false')
                    return
        
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
            union(set1,set2,set_node)
        
        for i in range(sw_num):
            if set_node[i]==[]:
                set_node.pop(i)
        if(len(set_node)==1):
            print('good topo')
        else:
            print('topo is not good, has ',len(set_node),'rigion')
            print(set_node)
            dp={k:len(v) for k,v in set_node.items() }
            max_index=sorted(dp.items(),key=lambda k:k[1])[-1][0]        
            alone_node=list(set(range(len(switch_adj_lst)))-set(set_node[max_index]))
            print(alone_node)
            degree=setting.degree
            for i in range(len(alone_node)):
                extend=[]
                for j in range(len(switch_adj_lst)):
                    if switch_adj_lst[alone_node[i]][j]==0 and alone_node[i]!=j and j not in alone_node:
                        extend.append(j)
                print('extend',extend,alone_node[i])
                now_degree=sum([1 if v >0 else 0 for v in switch_adj_lst[alone_node[i]]])
                if  now_degree>=degree:
                    switch_adj_lst[alone_node[i]][extend[0]]=\
                    (1 if random.random()<=probability else 0)*random.randint(sw_link_bw_range[0], sw_link_bw_range[1])#加权重
                    switch_adj_lst[extend[0]][alone_node[i]]=switch_adj_lst[alone_node[i]][extend[0]]
                else:
                    for s in range(degree-now_degree):
                        switch_adj_lst[alone_node[i]][extend[s]]=\
                            (1 if random.random()<=probability else 0)*random.randint(sw_link_bw_range[0], sw_link_bw_range[1])
                        switch_adj_lst[extend[s]][alone_node[i]]= switch_adj_lst[alone_node[i]][extend[s]]
                print(switch_adj_lst[alone_node[i]])
                    
        

    switch_adj_lst=[[0 for i in range(num_of_switch)] for j in range(num_of_switch)]

    #完全随机拓扑
    # for i in range(num_of_switch):
    #     for j in range(num_of_switch)[i+1:]:
    #         if j-i==1:
    #             switch_adj_lst[i][j]=random.randint(sw_link_bw_range[0], sw_link_bw_range[1])
    #             switch_adj_lst[j][i]=switch_adj_lst[i][j]
    #         else:
    #             switch_adj_lst[i][j]=(1 if random.random()<=probability else 0)*random.randint(sw_link_bw_range[0], sw_link_bw_range[1])
    #             switch_adj_lst[j][i]=switch_adj_lst[i][j]
    
    #随机拓扑
    # min_degree=setting.min_degree
    # ave_degree=setting.ave_degree
    # probability=setting.probability
    # for i in range(num_of_switch):
    #     for j in range(num_of_switch):
    #         if sum([1 if bw>0 else 0 for bw in switch_adj_lst[i]])> ave_degree:
    #             print(i,"enough",sum([1 if bw>0 else 0 for bw in switch_adj_lst[i]]))
    #             break
    #         else:
    #             print(i,"now",sum([1 if bw>0 else 0 for bw in switch_adj_lst[i]]))
    #             if(switch_adj_lst[i][j]==0 and i<j):
    #                 switch_adj_lst[i][j]=(1 if random.random()<=probability else 0)*random.randint(sw_link_bw_range[0], sw_link_bw_range[1])
    #                 switch_adj_lst[j][i]=switch_adj_lst[i][j]
    # for i in range(num_of_switch):
    #     print(i,sum([1 if bw>0 else 0 for bw in switch_adj_lst[i]]))
            
    # for i in range(num_of_switch):
    #     while sum([1 if bw>0 else 0 for bw in switch_adj_lst[i]])<min_degree:
    #         print(i,sum([1 if bw>0 else 0 for bw in switch_adj_lst[i]]))
    #         for j in range(num_of_switch):
    #             if switch_adj_lst[i][j]==0:
    #                 switch_adj_lst[i][j]=(1 if random.random()<=probability else 0)*random.randint(sw_link_bw_range[0], sw_link_bw_range[1])
    #                 switch_adj_lst[j][i]=switch_adj_lst[i][j]
    #             if sum([1 if bw>0 else 0 for bw in switch_adj_lst[i]])>=min_degree:
    #                 break
    # for i in range(num_of_switch):
    #     print("second test",i,sum([1 if bw>0 else 0 for bw in switch_adj_lst[i]]))
        
        
    #真实拓扑
    dir="../topo/"
    all_dir=os.listdir(dir)
    target=re.compile('node_'+str(num_of_switch)+'_link_*')
    file=None
    for data in all_dir:
        if re.match(target,data)!=None:
            file=data
            break
    f=open("../topo/"+file+"/links.txt",encoding="utf-8")
    for line in f.readlines():
        one=[int(i) for i in line.strip('\n').split('\t')[0:2]]
        x=one[0]-1
        y=one[1]-1
        if(switch_adj_lst[x][y]!=0 | switch_adj_lst[y][x]!=0):
            continue
        switch_adj_lst[x][y]=random.randint(sw_link_bw_range[0], sw_link_bw_range[1])
        switch_adj_lst[y][x]=switch_adj_lst[x][y]
    print(switch_adj_lst)
    f.close()

    
    
    user_sw_dic=dict()
    for i in range(num_of_user):
        if(i<num_of_switch):
            user_sw_dic[i]=i
        else:
            user_sw_dic[i]=int(random.random()*num_of_switch-0.1)
    # print(user_sw_dic)
    switch_comp_dic=dict()
    for i in range(num_of_switch):
        switch_comp_dic[i]=random.randint(sw_compute_range[0], sw_compute_range[1])
    # print(switch_comp_dic)
    
    topo_matrix=dict()
    topo_matrix['switch_adj_lst']=switch_adj_lst
    topo_matrix['user_sw_dic']=user_sw_dic
    topo_matrix['switch_comp_dic']=switch_comp_dic
    print('topo_info:',topo_matrix)
    
    # detect(switch_adj_lst)
    return topo_matrix

topo_info=topo_generator(num_of_switch,sw_conpute_range,sw_link_bw_range,num_of_user,probability)

    
nowpath=os.getcwd()
folder=os.path.exists(nowpath+'\\'+setting.path)
if not folder:
    os.mkdir(nowpath+'\\'+setting.path)
os.chdir(nowpath+'\\'+setting.path)

with open('topo_info.txt', 'wb') as f:
    pickle.dump(topo_info, f)