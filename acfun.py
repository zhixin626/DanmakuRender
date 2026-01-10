# -*- coding: utf-8 -*-

import json
import os
import time
from base64 import b64decode
from hashlib import sha1
from math import ceil
from mimetypes import guess_type

import requests
import subprocess
import os
from pathlib import Path

class AcFun(object):
    def __init__(self):
        self.session = requests.session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Origin": "https://member.acfun.cn",
            "Referer": "https://member.acfun.cn/video/upload",
            "X-Requested-With": "XMLHttpRequest"
        })
        self.LOGIN_URL    = "https://id.app.acfun.cn/rest/web/login/signin"
        self.TOKEN_URL    = "https://member.acfun.cn/video/api/getKSCloudToken"
        self.FRAGMENT_URL = "https://mediacloud.kuaishou.com/api/upload/fragment"
        self.COMPLETE_URL = "https://mediacloud.kuaishou.com/api/upload/complete"
        self.FINISH_URL   = "https://member.acfun.cn/video/api/uploadFinish"
        self.C_VIDEO_URL  = "https://member.acfun.cn/video/api/createVideo"
        self.C_DOUGA_URL  = "https://member.acfun.cn/video/api/createDouga"
        self.QINIU_URL    = "https://member.acfun.cn/common/api/getQiniuToken"
        self.QINIU_UP_URL = "https://upload.qiniup.com/"
        self.IMAGE_URL    = "https://imgs.aixifan.com/"
        self.GET_URL_AFTER= "https://member.acfun.cn/common/api/getUrlAfterUpload"

    @staticmethod
    def log(*msg: object):
        print(f'[{time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())}]', *msg)

    def calc_sha1(self, data: bytes) -> str:
        sha1_obj = sha1()
        sha1_obj.update(data)
        return sha1_obj.hexdigest()

    def login(self, username: str, password: str):
        r = self.session.post(
            url = self.LOGIN_URL,
            data = {
                'username': username,
                'password': password,
                'key': '',
                'captcha': ''
            }
        )
        if r.json()['result'] == 0:
            self.log('登陆成功')
        else:
            self.log("账号密码错误")
            os._exit(0)

    def get_token(self, filename: str, filesize: int) -> tuple:
        r = self.session.post(
            url=self.TOKEN_URL,
            data={
                "fileName": filename,
                "size": filesize,
                "template": "1"
            }
        )
        response = r.json()
        return response["taskId"], response["token"], response["uploadConfig"]["partSize"]

    def upload_chunk(self, block: bytes, fragment_id: int, upload_token: str):
        for _ in range(3):
            r = requests.post(
                url=self.FRAGMENT_URL,
                params={
                    "fragment_id": fragment_id,
                    "upload_token": upload_token
                },
                data=block
            )
            if r.json()["result"] == 1:
                self.log(f"分块{fragment_id+1}上传成功")
                return
            else:
                self.log(f"分块{fragment_id+1}上传失败，重试第{_+1}次", r.text)

    def complete(self, fragment_count: int, upload_token: str):
        r = requests.post(
            url=self.COMPLETE_URL,
            params={
                "fragment_count": fragment_count,
                "upload_token": upload_token
            }
        )
        if r.json()["result"] != 1:
            self.log(r.text)
    
    def upload_finish(self, taskId: int):
        r = self.session.post(
            url=self.FINISH_URL,
            data={
                "taskId": taskId
            }
        )
        if r.json()["result"] != 0:
            self.log(r.text)

    def create_video(self, video_key: int, filename: str) -> int:
        r = self.session.post(
            url=self.C_VIDEO_URL,
            data={
                "videoKey": video_key,
                "fileName": filename,
                "vodType": "ksCloud"
            }
        )
        response = r.json()
        if response["result"] != 0: #  or not response["videoId"]
            self.log(r.text)
        self.upload_finish(video_key)
        return response["videoId"]

    def create_douga(
        self,
        file_path: str, # 视频文件路径，建议绝对路径
        title: str, # 稿件标题
        channel_id: int, # 频道ID，查看：https://gist.github.com/Aruelius/69b60a141d38ce1e1bfcfe1104b98d62
        cover: str, # 视频封面图片路径，建议绝对路径
        desc: str = "", # 稿件简介
        tags: list = [], # 稿件标签
        creation_type: int = 1, # 1,转载 3,原创
        originalLinkUrl: str = "" # 转载来源
        ):

        file_name = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)
        task_id, token, part_size = self.get_token(file_name, file_size)
        fragment_count = ceil(file_size / part_size)
        self.log(f"{file_name} 开始上传, 一共{fragment_count}个分块")
        with open(file_path, "rb") as f:
            for fragment_id in range(fragment_count):
                chunk_data = f.read(part_size)
                if not chunk_data:
                    break
                self.upload_chunk(chunk_data, fragment_id, token)
            f.close()

        def add():
            video_id = self.create_video(task_id, file_name)
            data = {
                "title": title,
                "description": desc,
                "tagNames": json.dumps(tags),
                "creationType": creation_type,
                "channelId": channel_id,
                "coverUrl": self.cover(cover),
                "videoInfos": json.dumps([{"videoId": video_id,"title": title}]),
                "isJoinUpCollege": "0"
            }
            if creation_type == 1:
                data["originalLinkUrl"] = originalLinkUrl
                data["originalDeclare"] = "0"
            else:
                data["originalDeclare"] = "1"
            r = self.session.post(
                url=self.C_DOUGA_URL,
                data=data
            )
            response = r.json()
            if response["result"] == 0:
                self.log(f"视频投稿成功！AC号：{response['dougaId']}")
            else:
                self.log(r.text)
        
        self.complete(fragment_count, token)
        add()

    def cover(self, image_path: str):
        fname = os.path.basename(image_path)

        # 1) getQiniuToken (must be logged-in session)
        r = self.session.post(self.QINIU_URL, json={"fileName": fname})
        r.raise_for_status()
        info = r.json()["info"]

        upload_token = info["token"]
        host = info["httpEndpointList"][0]
        base = f"https://{host}/api/upload"

        resume_url   = f"{base}/resume"
        fragment_url = f"{base}/fragment"
        complete_url = f"{base}/complete"

        # 2) upload (do NOT need acfun cookies)
        rr = requests.get(resume_url, params={"upload_token": upload_token})
        rr.raise_for_status()

        with open(image_path, "rb") as f:
            image_data = f.read()

        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Cache-Control": "no-cache",
            "Origin": "https://member.acfun.cn",
            "Pragma": "no-cache",
            "Referer": "https://member.acfun.cn/",
            "Sec-Ch-Ua": '"Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "cross-site",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
        }

        rf = requests.post(
            fragment_url,
            params={"upload_token": upload_token,"fragment_id":0},
            data=image_data,
            headers=headers,
        )
        rf.raise_for_status()

        rc = requests.post(
            complete_url,
            params={"fragment_count": 1, "upload_token": upload_token},
            headers=headers,
        )
        rc.raise_for_status()

        # 3) getUrlAfterUpload (must be logged-in session)
        ra = self.session.post(
            self.GET_URL_AFTER,
            data={  # 注意：用 data，不用 json
                "bizFlag": "web-douga-cover",
                "token": upload_token,
                "fileName": fname,  # 可选，但建议带上
            },
        )
        ra.raise_for_status()
        j = ra.json()
        if j.get("result") != 0 or "url" not in j:
            raise RuntimeError(f"getUrlAfterUpload failed: {j}")

        return j["url"]

def extract_frame(video_path: str, output_path: str = None, time: str = "00:00:01") -> str:
    if output_path is None:
        video_dir = os.path.dirname(video_path) or "."
        video_name = Path(video_path).stem
        output_path = os.path.join(video_dir, "extract_frame.jpg")

    cmd = [
        "ffmpeg", "-y", "-ss", time, "-i", video_path,
        "-vframes", "1", "-q:v", "2", output_path
    ]

    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    return output_path

if __name__ == '__main__':
    acfun = AcFun()
    login_info="D:/DanmakuRender/.login_info/acfun.json"
    file_path="D:/DanmakuRender/Tasks文件/佐佐酱（转码后）/佐1月8日16点27分（转码后）_merged.mp4"
    title="佐佐酱1月8日直播回放"
    channel_id=218

    with open(login_info, "r", encoding="utf-8") as f:
        login_info = json.load(f)
    username = login_info.get("username")
    password = login_info.get("password")
    acfun.login(username=username, password=password)
    acfun.create_douga(
        file_path=file_path,
        title=title,
        channel_id=channel_id,
        creation_type=3,
        cover=extract_frame(file_path)
        )

