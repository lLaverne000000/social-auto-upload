import json


def sign_local(uri, data=None, a1="", web_session=""):
    raise RuntimeError("旧小红书非官方签名发布链路已禁用；请使用 sau xiaohongshu CLI")


def sign(uri, data=None, a1="", web_session=""):
    raise RuntimeError("旧小红书远程签名发布链路已禁用；请使用 sau xiaohongshu CLI")


def beauty_print(data: dict):
    print(json.dumps(data, ensure_ascii=False, indent=2))
