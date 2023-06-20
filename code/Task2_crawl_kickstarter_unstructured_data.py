import json
import os
import re
import time

from curl_cffi import requests
import html
from bs4 import BeautifulSoup
from loguru import logger
import threadpool
import threading

proxiesConfig = {
    "proxy_host" : "http-dynamic-S03.xiaoxiangdaili.com",
    "proxy_port" : 10030,
    "proxy_username" : "963691810268794880",
    "proxy_pwd" : "x33EI5Q5"
  }
proxy_host = proxiesConfig['proxy_host']
proxy_port = proxiesConfig['proxy_port']
proxy_username = proxiesConfig['proxy_username']
proxy_pwd = proxiesConfig['proxy_pwd']
proxyMeta = "http://%(user)s:%(pass)s@%(host)s:%(port)s" % {
    "host": proxy_host,
    "port": proxy_port,
    "user": proxy_username,
    "pass": proxy_pwd,
}
proxies = {
    'http': proxyMeta,
    'https': proxyMeta,
}



lock = threading.Lock()  # 普通锁


def startGetInfo(params):
    global detailDataList
    page = str(params[0])
    state = str(params[1])
    pledged = str(params[2])


    if state == 'successful':
        url = 'https://www.kickstarter.com/discover/advanced?state=successful&category_id=16&pledged='+pledged+'&sort=end_date&seed=2802262&page='+page
    else:
        url = 'https://www.kickstarter.com/discover/advanced?category_id=16&pledged='+pledged+'&raised=0&sort=end_date&seed=2802262&page='+page
    logger.info("获取分页数据线程启动，当前页数为:{}", page)
    while True:
        try:
            response = requests.get(url, impersonate="chrome104",proxies=proxies)
            jsonStrList = re.findall(r'data-project="(.+?)" data-ref="discovery_category_end', html.unescape(response.text))
            if 'meta name="csrf-token"' not in response.text:
                continue
            for jsonStr in jsonStrList:
                jsonData = json.loads(jsonStr)
                # print(json.dumps(jsonData))
                newUrl = jsonData['urls']['web']['project']
                slug = jsonData['slug']
                pid = jsonData['id']
                logger.info("分页数据获取成功：{}",[newUrl,slug,pid])
                lock.acquire()  # 加锁
                try:
                    detailDataList.append([newUrl,slug,pid,state])
                finally:
                    lock.release()  # 解锁
            break
        except:
            continue
    logger.info("当前拥有分类数量为：{}", len(detailDataList))
    logger.info("获取分页数据线程完毕，页数为:{}", page)

def startDo(params):
    global successIndex,failedIndex
    newUrl = params[0]
    slug = params[1]
    pid = params[2]
    state = params[3]
    logger.info("线程处理详情页数据线程启动，当前爬取数据为：{}",slug)
    # 爬取数据
    failedNum = 1
    while True:
        if failedNum==10:
            return
        try:
            session = requests.Session()
            response = session.get(newUrl, impersonate="chrome104", proxies=proxies)
            if 'Please verify you are a human' in str(html.unescape(response.text)):
                continue
            try:
                data_detail = json.loads(html.unescape(response.text).split(' window.current_project = "')[1].split('";')[0].replace('\\"','\"'))
            except:
                failedNum+=1
            break
        except:
            continue
    # print(html.unescape(response.text).split(' window.current_project = "')[1].split('";')[0].replace('\\"','\"'))
    # 获取字段
    try:
        video_url = data_detail['video']['base']
        # print(video_url)
    except:
        logger.info("数据：{}，没有视频，跳过",slug)
        return

    csrf = response.text.split('name="csrf-token" content="')[1].split('" />')[0]
    session.headers['x-csrf-token'] = csrf
    url = 'https://www.kickstarter.com/graph'
    data = [
        {
            "operationName": "Campaign",
            "variables": {
                "slug": slug
            },
            "query": "query Campaign($slug: String!) {\n  project(slug: $slug) {\n    id\n    isSharingProjectBudget\n    risks\n    story(assetWidth: 680)\n    currency\n    spreadsheet {\n      displayMode\n      public\n      url\n      data {\n        name\n        value\n        phase\n        rowNum\n        __typename\n      }\n      dataLastUpdatedAt\n      __typename\n    }\n    environmentalCommitments {\n      id\n      commitmentCategory\n      description\n      __typename\n    }\n    __typename\n  }\n}\n"
        },
        {
            "operationName": "FetchProjectSignalAndWatchStatus",
            "variables": {
                "pid": pid
            },
            "query": "query FetchProjectSignalAndWatchStatus($pid: Int) {\n  project(pid: $pid) {\n    ...project\n    __typename\n  }\n  me {\n    ...user\n    __typename\n  }\n}\n\nfragment project on Project {\n  id\n  pid\n  isDisliked\n  isLiked\n  isWatched\n  isWatchable\n  isLaunched\n  __typename\n}\n\nfragment user on User {\n  id\n  uid\n  canSeeConfirmWatchModal\n  canSeeConfirmSignalModal\n  isEmailVerified\n  __typename\n}\n"
        }
    ]
    while True:
        try:
            response = session.post(url, json=data, impersonate="chrome104", proxies=proxies)
            # 使用Beautiful Soup解析HTML
            soup = BeautifulSoup(response.json()[0]['data']['project']['story'], 'html.parser')
            break
        except:
            continue
    # 提取所有文本
    all_text = soup.get_text()
    # print(all_text)
    # 写出数据
    if state == 'successful':
        folder_path = root_path + state + '/' + str(successIndex)
        lock.acquire()  # 加锁
        try:
            successIndex+=1
        finally:
            lock.release()  # 解锁
    else:
        folder_path = root_path + state + '/' + str(failedIndex)
        lock.acquire()  # 加锁
        try:
            failedIndex += 1
        finally:
            lock.release()  # 解锁
    try:
        os.mkdir(folder_path)
    except:
        pass
    session.close()
    while True:
        try:
            session = requests.Session()
            video_res = session.get(video_url, impersonate="chrome104",timeout=500000)
            with open(folder_path + "/f1.mp4", "wb") as f:
                f.write(video_res.content)
            with open(folder_path + "/f1.txt", "w", encoding='utf-8') as f:
                f.write(all_text.encode().decode())
            logger.info("数据：{}，写出数据成功", slug)
            session.close()
            break
        except:
            session.close()
            continue
    logger.info("数据：{}，线程处理详情页数据线程完毕",slug)



if __name__ == '__main__':
    thread_num = 10 # 线程数
    startPageNum = 90  # 开始页码数
    endPageNum = 119  # 结束页码数
    allList = []
    detailDataList = []
    successIndex = 4265
    failedIndex = 4374
    root_path = 'D:/kickstarter_data/'
    # # 成功的
    # for pledged in ['3']:
    #     for i in range(startPageNum,endPageNum+1):
    #         allList.append([i,'successful',pledged])

    # 失败的
    for pledged in ['2']:
        for i in range(startPageNum, endPageNum + 1):
            allList.append([i, 'failed', pledged])

    # 多线程任务
    pool = threadpool.ThreadPool(thread_num)  # 创建线程池
    # print(json.dumps(accountInfoList))
    requests_do = threadpool.makeRequests(startGetInfo, allList)  # 创建任务
    [pool.putRequest(req) for req in requests_do]  # 加入任务
    pool.wait()  # 线程等待


    # 处理数据
    requests_do = threadpool.makeRequests(startDo, detailDataList)  # 创建任务
    [pool.putRequest(req) for req in requests_do]  # 加入任务
    pool.wait()  # 线程等待

    logger.info("程序执行完毕！")

