import euler
import requests
euler.install_thrift_import_hook()
from extract import get_video_playinfo
import os
import logging
logger = logging.getLogger(__name__)

def get_video_url(vid):
    try:
        external_preview_video_url = get_video_playinfo(vid).OriginalVideoInfo.MainUrl
    except:
        external_preview_video_url = ""
    return external_preview_video_url

def get_video_duration(vid):
    try:
        playinfo = get_video_playinfo(vid)
        return playinfo.Duration
    except:
        duration = None
    return duration

def get_poster_url(vid):
    try:
        external_preview_video_url = get_video_playinfo(vid).PosterUrl
    except:
        external_preview_video_url = ""
    return external_preview_video_url

def get_video_info(vid):
    
    try:
        video_info = get_video_playinfo(vid).OriginalVideoInfo
    except:
        video_info = None
    return video_info

def download_video(url, save_path):
    try:
        print(url, save_path)
        url = url.replace('http://', 'https://')
        if os.path.exists(save_path):
            return
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

        response = requests.get(url, headers=headers, stream=True)
        response.raise_for_status()

        with open(save_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        print(f"视频已保存到: {save_path}")
        return save_path
    except:
        import traceback;traceback.print_exc()
        return None

def get_video_url_by_vid(vid: str, target_resolution='') -> str:
    for attempt in range(3):
        try:
            vinfo = get_video_playinfo(vid)
        except Exception as e:
            logger.warning(f'[SegmentReplacementUtils] get video playinfo failed for vid={vid} at attempt {attempt+1} / 3, err: {e}, traceback: {traceback.format_exc()}')
            continue
        break

    # example: ['360p', '480p', '540p', '720p', '1080p']
    if target_resolution:
        for info in vinfo.VideoInfos:
            if info.VideoMeta.Definition == target_resolution:
                url = info.MainUrl
                duration = info.VideoMeta.Duration
                return url, duration

    # from small to big
    sorted_info = sorted(vinfo.VideoInfos, key = lambda info: int(info.VideoMeta.Definition[:-1]))
    if len(sorted_info) == 0:
        return vinfo.OriginalVideoInfo.MainUrl
    url = sorted_info[0].MainUrl
    duration = sorted_info[0].VideoMeta.Duration
    for info in sorted_info:
        if int(info.VideoMeta.Definition[:-1]) >= 540:
            url = info.MainUrl
            duration = info.VideoMeta.Duration
            break
    return url, duration


def download_vid_video(vid, output_path='./data/', target_resolution=''):
    output = os.path.join(output_path, f'{vid}.mp4')
    if os.path.exists(output):
        return output

    video_url, duration = get_video_url_by_vid(vid, target_resolution)
    # video_url = get_video_url(vid)
    output_path = download_video(video_url, os.path.join(output_path, f'{vid}.mp4'))
    return output_path

if __name__  == '__main__':
    # input_file = '/mnt/bn/maliva-gen-ai-v2/liwei.947/workspace/codes/p2v/parse_data/data/vids_0611.txt'
    # with open(input_file) as f:
    #     for line in f:
    #         vid = line.strip()
    #         download_vid_video(vid)
    # vids = set()
    # with open('1.txt') as f, open('2.txt', 'w') as wf:
    #     for line in f:
    #         vid = line.strip()
    #         vids.add(vid)
    #         wf.write('{}\n'.format(get_poster_url(vid)))
            
    download_video('https://v16m-default.tiktokcdn.com/a8cc29270a4855f7b8447ce315c240a7/6954f5cb/video/tos/alisg/tos-alisg-v-0051c001-sg/owoFWmHmXE1MBMDAi0Rw8hB9iofAAANACEhyA4/?a=0&bti=fHJmaWhkazF3dmdAb3FeXHBvbWJmK15g&ch=0&cr=0&dr=0&er=0&lr=default&cd=0%7C0%7C0%7C0&br=23196&bt=11598&cs=0&ds=4&ft=cApXJCz7ThWH32WNEGZmo0P&mime_type=video_mp4&qs=13&rc=M3Fqanc5cm42NTMzODYzNEBpM3Fqanc5cm42NTMzODYzNEBnXm1uMmRjLm1hLS1kMC1zYSNnXm1uMmRjLm1hLS1kMC1zcw%3D%3D&vvpl=1&l=02175680760061400000000000000000000ffff0a7b00ff6f5de4&btag=e00078000', '1.mp4')
                                
    # print(get_video_duration('v10033g50000cv3h5jfog65vlclbnhlg'))

