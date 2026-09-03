import euler
euler.install_thrift_import_hook()
from idls.toutiao.smart_player_thrift import SmartPlayerService, MGetPlayInfosRequest, UserInfo, VideoInfo,\
                                        MGetPlayInfosV2Response, PlayInfo, SmartPlayerService, FilterParams, \
                                        MGetPlayInfosV2Request, Identity, UrlParams
from idls.toutiao.guldan_thrift import GuldanService, GetMLFramesRequest, OptionParam, Option, FrameData
from idls.ad.creative_factory_thrift import CreativeFactoryService, GetUrlDataReq, GetUrlDataResp
from idls.base_thrift import Base
from rpc_utils import sign_rpc_request

def get_video_playinfo(vid: str) -> PlayInfo:
    user = UserInfo()
    caller = 'ad.site.phrase_factory'
    ak = '9e817982a791043a5d4397250d03be30'
    sk = '9ac744412af41c526d3eef5714c82404'
    method = 'MGetPlayInfosV2'
    local_ip = '10.19.45.195'
    extra = {
    }
    sig = sign_rpc_request(ak, sk, method, caller, extra=extra)
    if vid[:3] in ['v01', 'v02', 'v03', 'v0d']:
        target = "sd://toutiao.videoarch.smart_player?idc=hl&cluster=default"
    elif vid[:3] in ['v09', 'v12', 'v15']:
        target = "sd://toutiao.videoarch.smart_player?idc=maliva&cluster=default"
    # elif vid[:3] in ['v0f']:
    #     target = "sd://toutiao.videoarch.smart_player?idc=useast2a&cluster=default"
    else:
        target = "sd://toutiao.videoarch.smart_player?idc=maliva&cluster=default"

    try:
        client = euler.Client(SmartPlayerService, \
                                       target, timeout=3, transport="buffered")
        request = MGetPlayInfosV2Request(VIDs=[vid], FilterParams=FilterParams(), \
                                         User=user, UrlParams=UrlParams(Indate=3600 * 24 * 120), Identity=Identity(IdentityInfo=sig), \
                                         Base=Base(Caller=caller, Addr=local_ip))
        response = client.MGetPlayInfosV2(request)
        response:MGetPlayInfosV2Response
        # print(response)
        return response.VideoInfos[vid]
    except Exception as e:
        logger.error(f"get video play info failed, the error is {e}")
        raise GetVideoPlayInfoError(f"Get video: {vid} playinfo failed")