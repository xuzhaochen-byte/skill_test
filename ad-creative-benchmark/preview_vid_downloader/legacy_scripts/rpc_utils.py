import os
import base64
import hmac
import uuid
import codecs
import time
import logging
import magic
import bytedtos
import hashlib
import requests
import pypolaris
import euler
from euler import base_compat_middleware
from hashlib import sha1
from mimetypes import guess_extension, guess_type
from threading import Thread 
import types

logger = logging.getLogger(__name__)

DEBUG_MODE = os.environ.get('DEBUG_MODE', 'false').lower() == 'true'
AUTH_PREFIX_V1 = "VARCH1-HMAC-SHA1"
DEFAULT_TTL = 3600 # in seconds
SEP = ":"

if os.getenv('TCE_INTERNAL_IDC') == 'Aliyun_VA' or os.getenv('TCE_INTERNAL_IDC') == 'maliva':
    bucket = "ad-creative"
    accessKey = "JRGOLSMK48IYLYWKM9DS"
    url_prefix = 'https://sf16-muse-va.ibytedtos.com/obj/' + bucket + '/'
    tos_psm = "toutiao.tos.tosapi"
    tos_cluster = "default"
    tos_idc = "maliva"
else:
    bucket = "yais-sg"
    accessKey = "AYJSEJH37APVH4U26TCB"
    url_prefix = 'https://sf-tk-sg.ibytedtos.com/obj/' + bucket + '/'
    tos_psm = "toutiao.tos.tosapi"
    tos_cluster = "default"
    tos_idc = "sg1"


tos_client = bytedtos.Client(bucket, accessKey, service=tos_psm, cluster=tos_cluster, idc=tos_idc)
origin_req = tos_client._req
def new_req(self, method, key, body=None, headers=None, query=None, client_method=None):
    if headers is None:
        headers = {}
    headers["Destination-Service"] = self.service_name
    return origin_req(method, key, body, headers, query, client_method)
tos_client._req = types.MethodType(new_req, tos_client)
__SERVICES__ = {}


def get_or_create_service(clazz, target, transport="framed", timeout=10, without_cluster=False):
    key = "%s/%s" % (clazz, target)
    if key not in __SERVICES__:
        if "cluster" not in target and not without_cluster:
            without_cluster = True
        if euler.__version__ >= "2.0.0":
            client = euler.Client(clazz, target, transport=transport, timeout=timeout, without_cluster=without_cluster)
        else:
            client = euler.Client(clazz, target, transport=transport, timeout=timeout)
        client.use(base_compat_middleware.client_middleware)
        __SERVICES__[key] = client

    return __SERVICES__[key]


def gen_sign(nonce, app_secret, timestamp):
    keys = [str(nonce), str(app_secret), str(timestamp)]
    keys.sort()
    keystr = ''.join(keys)
    signature = hashlib.sha1(keystr.encode('utf-8')).hexdigest()
    return signature.lower()


def sign_rpc_request(ak, sk, method='', caller='', extra={}, ttl=0):
    """ sign_rpc_request
        ak: access_key
        sk: secret_key
    """
    if ttl <= 0:
        ttl = DEFAULT_TTL
    deadline = str(int(time.time())+ttl)

    arr = ['method='+method, 'caller='+caller, 'deadline='+deadline]
    arr.extend([k+'='+extra[k] for k in sorted(extra.keys())])


    raw = '&'.join(arr)
    hashed = hmac.new(codecs.encode(sk), codecs.encode(raw), sha1)
    dig = hashed.digest()
    ciphertext = base64.standard_b64encode(dig)
    return SEP.join([AUTH_PREFIX_V1, ak, deadline, codecs.decode(ciphertext)]) 


def async_func(f):
    def inner_func(*args, **kwargs):
        t = Thread(target=f, args=args, kwargs=kwargs)
        t.start()
    return inner_func


def gen_task_id():
    return int(uuid.uuid1().int >> 70)


def request_url(url, timeout=3):
    PROXIES = {
        "http": "http://creative_solution_webhook_tangweichao:bsZvwN9r28gTPw@10.8.14.17:8118",
        "https": "http://creative_solution_webhook_tangweichao:bsZvwN9r28gTPw@10.8.14.17:8118"
    }
    white_list = ['image-my.byted.org','image-my2.byted.org','image-sg.byted.org','p16-oec-ttp.tiktokcdn-us.com','p16-creative-tool-sg.tiktokcdn.com','image-va.byted.org']
    HEADERS = {"User-Agent": "Mozilla/5.0 (Windows; U; Windows NT 5.1; zh-CN; rv:1.9.1.6) "}
    resp = None
    logger.info(f'[debug] request url {url}')
    for i in range(2):
        try:
            if DEBUG_MODE:
                resp = requests.get(url, headers=HEADERS, stream=True, timeout=timeout)
            # elif "byted" in url or 'tiktok' in url:
            #     resp = requests.get(url, headers=HEADERS, stream=True, timeout=timeout)
            else:
                resp = pypolaris.safe_get(url, headers=HEADERS, stream=True, timeout=timeout,allow_domain=white_list)
        except Exception as e:
            logger.error(f"request url: {url} failed with error {e}\n, the response header is {resp.headers if resp else None}")
        if resp and resp.status_code == 200:
            return resp.content
    raise RuntimeError(f"get image return status code {resp.status_code if resp else None}")

def request_url_without_content(url, timeout=3):
    PROXIES = {
        "http": "http://creative_solution_webhook_tangweichao:bsZvwN9r28gTPw@10.8.14.17:8118",
        "https": "http://creative_solution_webhook_tangweichao:bsZvwN9r28gTPw@10.8.14.17:8118"
    }
    HEADERS = {"User-Agent": "Mozilla/5.0 (Windows; U; Windows NT 5.1; zh-CN; rv:1.9.1.6) "}
    resp = None
    for i in range(2):
        try:
            if "byted" in url:
                resp = requests.get(url, headers=HEADERS, stream=True, timeout=timeout)
            else:
                resp = pypolaris.safe_get(url, headers=HEADERS, stream=True, timeout=timeout)
        except Exception as e:
            logger.error(f"request url: {url} failed with error {e}\n, the response header is {resp.headers if resp else None}")
        if resp and resp.status_code == 200:
            return resp
    raise RuntimeError(f"get image return status code {resp.status_code if resp else None}")



def save_tos(content, object_name, overwrite=True):
    # avoid overwrite
    if not overwrite:
        try:
            tos_client.get_object(object_name)
            return url_prefix + object_name
        except:
            pass

    try:
        tos_client.put_object(object_name, content)
        url = url_prefix + object_name
    except Exception as e:
        logger.error('upload obj to tos error: ' + str(e), exc_info=1)
        raise RuntimeError('Upload obj to tos error')
    return url




def timer(func):
    """
    Timer cost wrapper.
    """
    def wrapper(*args, **kwargs):
        start_time = time.time()
        res = func(*args, **kwargs)
        end_time = time.time()
        logger.info(f"[{func.__name__}] cost time: {(end_time - start_time)}")
        # print(f"[{func.__name__}] cost time: {(end_time - start_time)}")
        filename = ''
        if func.__code__ and func.__code__.co_filename:
            filename = f'{func.__code__.co_filename}'.split('/')[-1]
            if filename and len(filename.split('.')) > 1:
                filename = filename.split('.')[0]
        ##METRICSCLIENT.emit_timer(f'url2video_timecost', end_time-start_time, tags={'url2video_timecost': f'{func.__name__}', 'file':f'{filename}'})
        return res
    return wrapper


LoggerConfig = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'default': {
            'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        },
    },
    'handlers': {
        'log_agent': {
            'level': 'INFO',
            'class': 'bytedlogger.StreamLogHandler',
            'tags': {},
            'formatter': 'default',
        },
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'default',
        },
    },
    'root': {
        'handlers': ['log_agent', 'console'],
        'level': "DEBUG",
    },
}


def get_logid_from_ctx(ctx):
    try:
        if ctx and hasattr(ctx, "persistent") and 'logid' in ctx.persistent:
            logid_from_ctx = ctx.persistent['logid']
            if isinstance(logid_from_ctx, bytes):
                logid = logid_from_ctx.decode('utf-8')
            elif isinstance(logid_from_ctx, str):
                logid = logid_from_ctx
        else:
            logid = ""
    except Exception as e:
        # local_logger.error(f"extract logid from ctx error, the error is {e}")
        logid = ""
    return logid




def finding_file_type_by_endswith(file_endswith: str) -> str:
    """
    Get file type.
    """
    file_type_mapping = {
        "video": [".mp4", ".mov", ".avi", ".wmv", ".mpg", ".mpeg", ".rm", ".ram", ".swf", ".flv"],
        "image": [".jpg", ".jpeg", ".png", ".gif", ".tiff", ".raw"],
        "audio": [".mp3", ".wav", ".aac", ".flac"]
    }
    for file_type in file_type_mapping:
        if file_endswith.lower() in file_type_mapping[file_type]:
            return file_type
    return "unknow"

if __name__ == '__main__':
    with open('i2v_inpaint_output_v10033g50000cv3h5jfog65vlclbnhlg_6823.mp4', 'rb') as f:
        data = f.read()
    print(save_tos(data, 'lw_test_04302.mp4'))