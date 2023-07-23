import json
import requests
import time
from io import BytesIO
from http import HTTPStatus
from websocket import create_connection
from websocket._exceptions import WebSocketConnectionClosedException
import ssl
from PIL import Image

import src.proxy as proxy

from loguru import logger
from bs4 import BeautifulSoup

def set_pixel(self, coord, color_index, canvas_index, access_token):
    # ACCEPTS REDDIT API COORD
    url = "https://gql-realtime-2.reddit.com/query"

    payload = json.dumps(
        {
            "operationName": "setPixel",
            "variables": {
                "input": {
                    "actionName": "r/replace:set_pixel",
                    "PixelMessageData": {
                        "coordinate": {"x": coord[0], "y": coord[1]},
                        "colorIndex": color_index,
                        "canvasIndex": canvas_index,
                    },
                }
            },
            "query": """mutation setPixel($input: ActInput!) {
                    act(input: $input) {
                        data {
                            ... on BasicMessage {
                                id
                                data {
                                    ... on GetUserCooldownResponseMessageData {
                                        nextAvailablePixelTimestamp
                                        __typename
                                    }
                                    ... on SetPixelResponseMessageData {
                                        timestamp
                                        __typename
                                    }
                                    __typename
                                }
                                __typename
                            }
                            __typename
                        }
                        __typename
                    }
                }
            """,
        }
    )
    headers = {
        "origin": "https://garlic-bread.reddit.com",
        "referer": "https://garlic-bread.reddit.com/",
        "apollographql-client-name": "garlic-bread",
        "Authorization": "Bearer " + access_token,
        "Content-Type": "application/json",
    }

    response = requests.request(
        "POST",
        url,
        headers=headers,
        data=payload,
        proxies=proxy.get_random_proxy(self, username=None),
    )

    return response

def get_board(self, access_token_in):
        logger.debug("Connecting and obtaining board images")
        while not self.stop_event.is_set():
            try:
                ws = create_connection(
                    "wss://gql-realtime-2.reddit.com/query",
                    origin="https://garlic-bread.reddit.com",
                    sslopt={"cert_reqs": ssl.CERT_NONE},
                    
                )
                break
            except Exception:
                logger.error(
                    "Failed to connect to websocket, trying again in 30 seconds..."
                )
                time.sleep(30)

        ws.send(
            json.dumps(
                {
                    "type": "connection_init",
                    "payload": {"Authorization": "Bearer " + access_token_in},
                }
            )
        )
        while not self.stop_event.is_set():
            try:
                msg = ws.recv()
            except WebSocketConnectionClosedException as e:
                logger.error(e)
                continue
            if msg is None:
                logger.error("Reddit failed to acknowledge connection_init")
                exit()
            if msg.startswith('{"type":"connection_ack"}'):
                logger.debug("Connected to WebSocket server")
                break
        logger.debug("Obtaining Canvas information")
        ws.send(
            json.dumps(
                {
                    "id": "1",
                    "type": "start",
                    "payload": {
                        "variables": {
                            "input": {
                                "channel": {
                                    "teamOwner": "GARLICBREAD",
                                    "category": "CONFIG",
                                }
                            }
                        },
                        "extensions": {},
                        "operationName": "configuration",
                        "query": "subscription configuration($input: SubscribeInput!) {\n  subscribe(input: $input) {\n    id\n    ... on BasicMessage {\n      data {\n        __typename\n        ... on ConfigurationMessageData {\n          colorPalette {\n            colors {\n              hex\n              index\n              __typename\n            }\n            __typename\n          }\n          canvasConfigurations {\n            index\n            dx\n            dy\n            __typename\n          }\n          canvasWidth\n          canvasHeight\n          __typename\n        }\n      }\n      __typename\n    }\n    __typename\n  }\n}\n",
                    },
                }
            )
        )

        while not self.stop_event.is_set():
            canvas_payload = json.loads(ws.recv())
            if canvas_payload["type"] == "data":
                canvas_details = canvas_payload["payload"]["data"]["subscribe"]["data"]
                logger.debug("Canvas config: {}", canvas_payload)
                break

        canvas_sockets = []

        canvas_count = len(canvas_details["canvasConfigurations"])

        for i in range(0, canvas_count):
            canvas_sockets.append(2 + i)
            logger.debug("Creating canvas socket {}", canvas_sockets[i])

            ws.send(
                json.dumps(
                    {
                        "id": str(2 + i),
                        "type": "start",
                        "payload": {
                            "variables": {
                                "input": {
                                    "channel": {
                                        "teamOwner": "GARLICBREAD",
                                        "category": "CANVAS",
                                        "tag": str(i),
                                    }
                                }
                            },
                            "extensions": {},
                            "operationName": "replace",
                            "query": """subscription replace($input: SubscribeInput!) {
                                    subscribe(input: $input) {
                                        id
                                        ... on BasicMessage {
                                            data {
                                                __typename
                                                ... on FullFrameMessageData {
                                                    __typename
                                                    name
                                                    timestamp
                                                }
                                                ... on DiffFrameMessageData {
                                                    __typename
                                                    name
                                                    currentTimestamp
                                                    previousTimestamp
                                                }
                                            }
                                            __typename
                                        }
                                        __typename
                                    }
                                }""",
                        },
                    }
                )
            )

        imgs = []
        logger.debug("A total of {} canvas sockets opened", len(canvas_sockets))

        while len(canvas_sockets) > 0:
            temp = json.loads(ws.recv())
            logger.debug("Waiting for WebSocket message")

            if temp["type"] == "data":
                logger.debug(f"Received WebSocket data type message")
                msg = temp["payload"]["data"]["subscribe"]

                if msg["data"]["__typename"] == "FullFrameMessageData":
                    logger.debug("Received full frame message")
                    img_id = int(temp["id"])
                    logger.debug("Image ID: {}", img_id)

                    if img_id in canvas_sockets:
                        logger.debug("Getting image: {}", msg["data"]["name"])
                        img = requests.get(msg["data"]["name"], stream=True,
                                           proxies=proxy.get_random_proxy(self, username=None),)
                        if not img.status_code == 404:
                            imgs.append(
                                [
                                    img_id,
                                    Image.open(
                                        BytesIO(img.content)
                                    ),
                                ]
                            )
                            canvas_sockets.remove(img_id)
                            logger.debug(
                                "Canvas sockets remaining: {}", len(canvas_sockets)
                            )
                        else:
                            logger.debug("Received wrong image")
                            canvas_sockets.remove(img_id)

        for i in range(0, canvas_count - 1):
            ws.send(json.dumps({"id": str(2 + i), "type": "stop"}))

        ws.close()

        new_img_width = (
            max(map(lambda x: x["dx"], canvas_details["canvasConfigurations"]))
            + canvas_details["canvasWidth"]
        )
        logger.debug("New image width: {}", new_img_width)

        new_img_height = (
            max(map(lambda x: x["dy"], canvas_details["canvasConfigurations"]))
            + canvas_details["canvasHeight"]
        )
        logger.debug("New image height: {}", new_img_height)

        new_img = Image.new("RGB", (new_img_width, new_img_height))

        for idx, img in enumerate(sorted(imgs, key=lambda x: x[0])):
            logger.debug("Adding image (ID {}): {}", img[0], img[1])
            dx_offset = int(canvas_details["canvasConfigurations"][idx]["dx"])
            dy_offset = int(canvas_details["canvasConfigurations"][idx]["dy"])
            new_img.paste(img[1], (dx_offset, dy_offset))

        return new_img

def login(self, username, password, index, current_time):
    while not self.stop_event.is_set():
        try:
            client = requests.Session()
            client.proxies = proxy.get_random_proxy(self, username)
            client.headers.update(
                {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
                    "Origin": "https://www.reddit.com",
                    "Referer": "https://www.reddit.com/login/?",
                }
            )

            client.get("https://www.reddit.com")

            r = client.get(
                "https://www.reddit.com/login",
                proxies=proxy.get_random_proxy(self, username),
            )
            login_get_soup = BeautifulSoup(r.content, "html.parser")
            csrf_token = login_get_soup.find(
                "input", {"name": "csrf_token"}
            )["value"]
            data = {
                "username": username,
                "password": password,
                "dest": "https://new.reddit.com/",
                "csrf_token": csrf_token,
                "otp": "",
            }

            r = client.post(
                "https://www.reddit.com/login",
                data=data,
                proxies=proxy.get_random_proxy(self, username),
            )
            break
        except Exception:
            logger.error(
                "Failed to connect to websocket, trying again in 30 seconds..."
            )
            time.sleep(30)

    if r.status_code != HTTPStatus.OK.value:
        # password is probably invalid
        logger.exception("{} - Authorization failed!", username)
        logger.debug("response: {} - {}", r.status_code, r.text)
        return
    else:
        logger.success("{} - Authorization successful!", username)
    logger.debug("Obtaining access token...")
    r = client.get(
        "https://new.reddit.com/",
        proxies=proxy.get_random_proxy(self, username),
    )
    data_str = (
        BeautifulSoup(r.content, features="html.parser")
        .find("script", {"id": "data"})
        .contents[0][len("window.__r = ") : -1]
    )
    data = json.loads(data_str)
    response_data = data["user"]["session"]

    if "error" in response_data:
        logger.error(
            "An error occured. Make sure you have the correct credentials. Response data: {}",
            response_data,
        )
        exit(1)

    self.access_tokens[index] = response_data["accessToken"]
    # access_token_type = data["user"]["session"]["accessToken"]  # this is just "bearer"
    access_token_expires_in_seconds = response_data[
        "expiresIn"
    ]  # this is usually "3600"
    # access_token_scope = response_data["scope"]  # this is usually "*"

    # ts stores the time in seconds
    self.access_token_expires_at_timestamp[
        index
    ] = current_time + access_token_expires_in_seconds
    logger.debug(
        "Received new access token: {}************",
        self.access_tokens.get(index)[:5],
    )