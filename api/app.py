from fastapi import FastAPI, HTTPException, Query
from fastapi import Security, Depends
from fastapi.responses import StreamingResponse
from fastapi.security.api_key import APIKeyHeader
import requests
from parsel import Selector
import re
import io
import os
import uuid 
import traceback
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(
    title="TikTok Video Downloader API",
    description="API for downloading TikTok videos without watermarks",
    version="1.0.0"
)

API_KEY = os.getenv("API_KEY")
if not API_KEY:
    raise RuntimeError("❌ ERROR: API_KEY no está definida. La API no puede arrancar.")
else:
    print(f"✅ API_KEY cargada correctamente")

api_key_header = APIKeyHeader(name="X-API-Key")

async def get_api_key(api_key_header: str = Security(api_key_header)):
    if api_key_header != API_KEY:
        raise HTTPException(
            status_code=403,
            detail="Could not validate API key"
        )
    return api_key_header

# Create a directory for temporary storage if needed
os.makedirs("temp_videos", exist_ok=True)

# Headers to mimic a browser
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Cache-Control': 'max-age=0',
}

@app.get("/download")
async def download_video(url: str = Query(..., description="TikTok video URL"),
                         api_key: str = Depends(get_api_key)
):
    """Download TikTok video using multiple methods, trying each until one works"""
    print(f"Received download request for URL: {url}")
    
    # Try each method in sequence until one works
    methods = [download_v2, download_v3, download_v1]
    last_error = None
    
    for method in methods:
        try:
            print(f"Trying download method: {method.__name__}")
            return await method(url)
        except Exception as e:
            logger.warning(f"Method {method.__name__} failed: {str(e)}")
            last_error = e
    
    # If we got here, all methods failed
    logger.error(f"All download methods failed for URL: {url}")
    if isinstance(last_error, HTTPException):
        raise last_error
    else:
        error_details = f"All download methods failed. Last error: {str(last_error)}"
        logger.error(error_details)
        logger.debug(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=error_details)

def extract_video_id(url):
    """Extract username and video ID from a TikTok URL"""
    print(f"Extracting video ID from URL: {url}")
    
    # Handle shortened URLs
    if 'vm.tiktok.com' in url or 'vt.tiktok.com' in url:
        print("Detected shortened URL, following redirect...")
        try:
            response = requests.get(url, headers=headers, allow_redirects=True)
            url = response.url
            print(f"Redirected to: {url}")
        except Exception as e:
            logger.error(f"Error following redirect: {str(e)}")
            raise HTTPException(status_code=400, detail=f"Failed to follow redirect: {str(e)}")

    # First try the standard pattern
    username_pattern = r"@([A-Za-z0-9_.]+)"
    content_type_pattern = r"/(video|photo)/(\d+)"
    
    username_match = re.search(username_pattern, url)
    content_type_match = re.search(content_type_pattern, url)
    
    # If standard pattern fails, try alternative patterns
    if not username_match or not content_type_match:
        logger.warning("Standard pattern failed, trying alternative patterns...")
        
        # Try to extract video ID from any digit sequence
        alt_video_id_match = re.search(r"[/=](\d{15,})", url)
        if alt_video_id_match:
            video_id = alt_video_id_match.group(1)
            print(f"Found video ID using alternative pattern: {video_id}")
            
            # Try to extract username or use placeholder
            if username_match:
                username = username_match.group(0)
            else:
                username = "@user"
                logger.warning(f"Couldn't extract username, using placeholder: {username}")
            
            return username, video_id, "video"
    
    if not username_match:
        logger.error(f"Could not extract username from URL: {url}")
        raise HTTPException(status_code=400, detail="Could not extract username from URL")
    
    username = username_match.group(0)
    print(f"Extracted username: {username}")
    
    if not content_type_match:
        logger.error(f"Could not extract video ID from URL: {url}")
        raise HTTPException(status_code=400, detail="Could not extract video ID from URL")
    
    content_type = content_type_match.group(1)
    video_id = content_type_match.group(2)
    print(f"Extracted content type: {content_type}, video ID: {video_id}")
    
    return username, video_id, content_type

async def download_v1(url: str):
    """Download TikTok video using tmate.cc (v1 method)"""
    print(f"Starting v1 download method for URL: {url}")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:107.0) Gecko/20100101 Firefox/107.4',
        'Content-Type': 'application/x-www-form-urlencoded',
        'Origin': 'https://tmate.cc',
        'Connection': 'keep-alive',
        'Referer': 'https://tmate.cc/',
        'Sec-Fetch-Site': 'same-origin',
    }

    try:
        _, video_id, content_type = extract_video_id(url)
        
        if content_type != "video":
            logger.warning(f"Content type {content_type} is not supported, only videos")
            raise HTTPException(status_code=400, detail="Only video downloads are supported")

        with requests.Session() as s:
            # First request to get the token
            logger.debug("Making initial request to tmate.cc")
            response = s.get("https://tmate.cc/", headers=headers)
            
            if response.status_code != 200:
                logger.error(f"Initial request to tmate.cc failed with status code: {response.status_code}")
                raise HTTPException(status_code=response.status_code, detail=f"tmate.cc initial request failed with status {response.status_code}")
            
            selector = Selector(response.text)
            token = selector.css('input[name="token"]::attr(value)').get()
            
            if not token:
                logger.error("Could not retrieve token from tmate.cc")
                raise HTTPException(status_code=500, detail="Could not retrieve token from tmate.cc")
            
            logger.debug(f"Obtained token: {token}")
            
            # Submit the video URL
            data = {'url': url, 'token': token}
            logger.debug(f"Submitting URL to tmate.cc: {url}")
            response = s.post('https://tmate.cc/action', headers=headers, data=data)
            
            if response.status_code != 200:
                logger.error(f"tmate.cc action request failed with status code: {response.status_code}")
                raise HTTPException(status_code=response.status_code, detail=f"tmate.cc action request failed with status {response.status_code}")
            
            try:
                response_json = response.json()
            except Exception as e:
                logger.error(f"Failed to parse JSON response: {str(e)}")
                logger.debug(f"Response content: {response.text[:500]}")
                raise HTTPException(status_code=500, detail=f"Invalid JSON response from tmate.cc: {str(e)}")
                
            response_data = response_json.get("data")
            if not response_data:
                logger.error("Invalid response from tmate.cc (no data field)")
                logger.debug(f"Response JSON: {response_json}")
                raise HTTPException(status_code=500, detail="Invalid response from tmate.cc (no data field)")
            
            selector = Selector(text=response_data)
            
            # Get the no-watermark video link (first link)
            download_links = selector.css('.downtmate-right.is-desktop-only.right a::attr(href)').getall()
            if not download_links or len(download_links) < 1:
                logger.error("Could not find download link in tmate.cc response")
                logger.debug(f"Response data: {response_data[:500]}")
                raise HTTPException(status_code=500, detail="Could not find download link")
            
            download_link = download_links[0]  # No watermark version
            logger.debug(f"Found download link: {download_link}")
            
            # Download the video content
            logger.debug("Downloading video content")
            video_response = s.get(download_link, stream=True, headers=headers)
            
            if video_response.status_code != 200:
                logger.error(f"Failed to download video with status code: {video_response.status_code}")
                raise HTTPException(status_code=video_response.status_code, 
                                  detail=f"Failed to download video from tmate.cc with status {video_response.status_code}")
            
            # Check if we got valid video content
            content_type = video_response.headers.get('Content-Type', '')
            if 'video' not in content_type and 'octet-stream' not in content_type:
                logger.warning(f"Unexpected content type in response: {content_type}")
                logger.debug(f"Response headers: {dict(video_response.headers)}")
                
            content_length = video_response.headers.get('Content-Length', 0)
            logger.debug(f"Video content length: {content_length} bytes")
            
            # Create a streaming response with the video content
            print(f"Successfully downloaded video using v1 method, returning {content_length} bytes")
            return StreamingResponse(
                io.BytesIO(video_response.content),
                media_type="video/mp4",
                headers={
                    "Content-Disposition": f'attachment; filename="tiktok_{video_id}.mp4"',
                    "Content-Length": str(len(response.content)),
                    "Accept-Ranges": "bytes"
                }
            )
    
    except Exception as e:
        if isinstance(e, HTTPException):
            logger.error(f"HTTP exception in v1 method: {e.detail}")
            raise e
        logger.error(f"Error in v1 method: {str(e)}")
        logger.debug(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Error downloading video (v1): {str(e)}")

async def download_v2(url: str):
    """Download TikTok video using musicaldown.com (v2 method)"""
    print(f"Starting v2 download method for URL: {url}")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Sec-Fetch-Site': 'same-origin',
        'Content-Type': 'application/x-www-form-urlencoded',
        'Origin': 'https://musicaldown.com',
        'Connection': 'keep-alive',
        'Referer': 'https://musicaldown.com/en?ref=more',
    }

    try:
        _, video_id, content_type = extract_video_id(url)
        
        if content_type != "video":
            logger.warning(f"Content type {content_type} is not supported, only videos")
            raise HTTPException(status_code=400, detail="Only video downloads are supported")

        with requests.Session() as s:
            # First request to get the tokens
            logger.debug("Making initial request to musicaldown.com")
            r = s.get("https://musicaldown.com/en", headers=headers)
            
            if r.status_code != 200:
                logger.error(f"Initial request to musicaldown.com failed with status code: {r.status_code}")
                raise HTTPException(status_code=r.status_code, 
                                   detail=f"musicaldown.com initial request failed with status {r.status_code}")
            
            selector = Selector(text=r.text)

            token_a = selector.xpath('//*[@id="link_url"]/@name').get()
            token_b = selector.xpath('//*[@id="submit-form"]/div/div[1]/input[2]/@name').get()
            token_b_value = selector.xpath('//*[@id="submit-form"]/div/div[1]/input[2]/@value').get()
            
            if not token_a or not token_b or not token_b_value:
                logger.error("Could not retrieve tokens from musicaldown.com")
                logger.debug(f"HTML content: {r.text[:500]}")
                raise HTTPException(status_code=500, detail="Could not retrieve tokens from musicaldown.com")

            logger.debug(f"Obtained tokens: {token_a}, {token_b}={token_b_value}")
            
            data = {
                token_a: url,
                token_b: token_b_value,
                'verify': '1',
            }
            
            # Submit the form to get download links
            logger.debug(f"Submitting URL to musicaldown.com: {url}")
            response = s.post('https://musicaldown.com/download', headers=headers, data=data)
            
            if response.status_code != 200:
                logger.error(f"musicaldown.com download request failed with status code: {response.status_code}")
                raise HTTPException(status_code=response.status_code, 
                                   detail=f"musicaldown.com download request failed with status {response.status_code}")
            
            selector = Selector(text=response.text)
            
            # Attempt to get various possible download link selectors
            download_link = None
            selectors_to_try = [
                '/html/body/div[2]/div/div[2]/div[2]/a[1]/@href',  # Original selector
                '//div[contains(@class, "row")]//a[contains(text(), "Download")]/@href',  # Generic download button
                '//a[contains(@href, ".mp4")]/@href'  # Any MP4 link
            ]
            
            for selector_path in selectors_to_try:
                download_link = selector.xpath(selector_path).get()
                if download_link:
                    logger.debug(f"Found download link using selector {selector_path}: {download_link}")
                    break
            
            if not download_link:
                logger.error("Could not find download link in musicaldown.com response")
                logger.debug(f"Response content: {response.text[:500]}")
                raise HTTPException(status_code=500, detail="Could not find download link")
            
            # Download the video content
            logger.debug(f"Downloading video from: {download_link}")
            response = s.get(download_link, stream=True, headers=headers)
            
            if response.status_code != 200:
                logger.error(f"Failed to download video with status code: {response.status_code}")
                raise HTTPException(status_code=response.status_code, 
                                   detail=f"Failed to download video from musicaldown.com with status {response.status_code}")
            
            # Check if we got valid video content
            content_type = response.headers.get('Content-Type', '')
            if 'video' not in content_type and 'octet-stream' not in content_type:
                logger.warning(f"Unexpected content type in response: {content_type}")
                logger.debug(f"Response headers: {dict(response.headers)}")
                
            content_length = response.headers.get('Content-Length', 0)
            logger.debug(f"Video content length: {content_length} bytes")
            
            # Create a streaming response with the video content
            print(f"Successfully downloaded video using v2 method, returning {content_length} bytes")
            return StreamingResponse(
                io.BytesIO(response.content),
                media_type="video/mp4",
                headers={
                    "Content-Disposition": f'attachment; filename="tiktok_{video_id}.mp4"'
                }
            )
    
    except Exception as e:
        if isinstance(e, HTTPException):
            logger.error(f"HTTP exception in v2 method: {e.detail}")
            raise e
        logger.error(f"Error in v2 method: {str(e)}")
        logger.debug(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Error downloading video (v2): {str(e)}")

async def download_v3(url: str):
    """Download TikTok video using tiktokio.com (v3 method)"""
    print(f"Starting v3 download method for URL: {url}")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.5',
        'HX-Request': 'true',
        'HX-Trigger': 'search-btn',
        'HX-Target': 'tiktok-parse-result',
        'HX-Current-URL': 'https://tiktokio.com/',
        'Content-Type': 'application/x-www-form-urlencoded',
        'Origin': 'https://tiktokio.com',
        'Connection': 'keep-alive',
        'Referer': 'https://tiktokio.com/'
    }

    try:
        _, video_id, content_type = extract_video_id(url)
        
        if content_type != "video":
            logger.warning(f"Content type {content_type} is not supported, only videos")
            raise HTTPException(status_code=400, detail="Only video downloads are supported")

        with requests.Session() as s:
            # First request to get the prefix
            logger.debug("Making initial request to tiktokio.com")
            r = s.get("https://tiktokio.com/", headers=headers)
            
            if r.status_code != 200:
                logger.error(f"Initial request to tiktokio.com failed with status code: {r.status_code}")
                raise HTTPException(status_code=r.status_code, 
                                   detail=f"tiktokio.com initial request failed with status {r.status_code}")
            
            selector = Selector(text=r.text)
            prefix = selector.css('input[name="prefix"]::attr(value)').get()
            
            if not prefix:
                logger.error("Could not retrieve prefix from tiktokio.com")
                logger.debug(f"HTML content: {r.text[:500]}")
                raise HTTPException(status_code=500, detail="Could not retrieve prefix from tiktokio.com")

            logger.debug(f"Obtained prefix: {prefix}")
            
            data = {
                'prefix': prefix,
                'vid': url,
            }
            
            # Submit the form to get download links
            logger.debug(f"Submitting URL to tiktokio.com API: {url}")
            response = s.post('https://tiktokio.com/api/v1/tk-htmx', headers=headers, data=data)
            
            if response.status_code != 200:
                logger.error(f"tiktokio.com API request failed with status code: {response.status_code}")
                raise HTTPException(status_code=response.status_code, 
                                   detail=f"tiktokio.com API request failed with status {response.status_code}")
            
            # Debug the response content
            logger.debug(f"API response content: {response.text[:500]}")
            
            selector = Selector(text=response.text)
            
            # Get the no-watermark video link (first link)
            download_links = selector.css('div.tk-down-link a::attr(href)').getall()
            
            if not download_links or len(download_links) < 1:
                logger.error("Could not find download link in tiktokio.com response")
                # Try alternative selectors
                download_links = selector.css('a[href*=".mp4"]::attr(href)').getall()
                if not download_links or len(download_links) < 1:
                    logger.error("Could not find download link with alternative selector")
                    logger.debug(f"Response content: {response.text}")
                    raise HTTPException(status_code=500, detail="Could not find download link")
            
            download_link = download_links[0]  # No watermark version
            logger.debug(f"Found download link: {download_link}")
            
            # Download the video content
            logger.debug(f"Downloading video from: {download_link}")
            response = s.get(download_link, stream=True, headers=headers)
            
            if response.status_code != 200:
                logger.error(f"Failed to download video with status code: {response.status_code}")
                raise HTTPException(status_code=response.status_code, 
                                   detail=f"Failed to download video from tiktokio.com with status {response.status_code}")
            
            # Check if we got valid video content
            content_type = response.headers.get('Content-Type', '')
            if 'video' not in content_type and 'octet-stream' not in content_type:
                logger.warning(f"Unexpected content type in response: {content_type}")
                logger.debug(f"Response headers: {dict(response.headers)}")
                
            content_length = response.headers.get('Content-Length', 0)
            logger.debug(f"Video content length: {content_length} bytes")
            
            # Create a streaming response with the video content
            print(f"Successfully downloaded video using v3 method, returning {content_length} bytes")
            return StreamingResponse(
                io.BytesIO(response.content),
                media_type="video/mp4",
                headers={
                    "Content-Disposition": f'attachment; filename="tiktok_{video_id}.mp4"'
                }
            )
    
    except Exception as e:
        if isinstance(e, HTTPException):
            logger.error(f"HTTP exception in v3 method: {e.detail}")
            raise e
        logger.error(f"Error in v3 method: {str(e)}")
        logger.debug(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Error downloading video (v3): {str(e)}")