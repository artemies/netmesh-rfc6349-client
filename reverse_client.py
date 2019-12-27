import asyncio
import websockets
import subprocess
import re
import sys
import json
import os, stat
from utilities import client_utils
import windows_scan
from constants import *
import traceback
from datetime import datetime
from log_settings import getStreamLogger, getFileLogger

LOGFILE = datetime.today().strftime('logfiles/%Y-%m-%d-%H-%M-%S.log')
FALLBACK_LOGGER = getStreamLogger()

'''
        retrieves the MTU from the server-side handler
'''
async def mtu_process(SERVER_IP, handler_port, udp_port, logger=FALLBACK_LOGGER):
    plpmtu_process = None
    try:
        websocket_url = "ws://"+SERVER_IP+":"+str(handler_port)
        async with websockets.connect(websocket_url) as websocket:
            await websocket.send("start")
            go_signal = await websocket.recv()
            fname = "tempfiles/reverse_mode/mtu_temp_file"
            tempfile = open(fname,"w+")
            plpmtu_process = subprocess.Popen(["sudo",
                                               "./plpmtu-reverse",
                                               "-p", "udp",
                                               "-s", SERVER_IP+":"+udp_port
                                              ], stdout = tempfile)
            mtu = await websocket.recv()
            try:
                plpmtu_process.kill()
            except:
                pass
            return mtu
    except:
        try:
            logger.error(("connection error"))
            plpmtu_process.kill()
        except:
            pass
    return

'''
        retrieves the RTT from the server-side handler
'''
async def rtt_process(SERVER_IP, handler_port, logger=FALLBACK_LOGGER):
    rtt_process = None
    try:
        websocket_url = "ws://"+SERVER_IP+":"+str(handler_port)
        async with websockets.connect(websocket_url) as websocket:
            await websocket.send(SERVER_IP)
            go_signal = await websocket.recv()
            rtt_process = subprocess.Popen(["python3",
                                             "udp-ping-client.py",
                                             SERVER_IP
                                            ])
            rtt = await websocket.recv()
            try:
                rtt_process.kill()
            except:
                pass
            return rtt
    except:
        try:
            logger.error(("connection error"))
            rtt_process.kill()
        except:
            pass
    return

'''
        retrieves the Baseline Bandwidth from the server-side handler
'''
async def bandwidth_process(SERVER_IP, handler_port, bandwidth_port, rtt, logger=FALLBACK_LOGGER):
    bb_process = None
    try:
        websocket_url = "ws://"+SERVER_IP+":"+str(handler_port)
        async with websockets.connect(websocket_url) as websocket:
            await websocket.send(rtt)
            go_signal = await websocket.recv()
            bb_process = subprocess.Popen(["iperf3",
                                           "--client", SERVER_IP,
                                           "--udp",
                                           "--reverse",
                                           "--bandwidth", "100M",
                                           "--time", "10",
                                           "--port", str(bandwidth_port),
                                           "--format", "m"], stdout = subprocess.PIPE)
            bb_process.wait()
            await websocket.send("stop")
            bb = await websocket.recv()
            try:
                bb_process.kill()
            except:
                pass
            return bb
    except:
        try:
            logger.error(("connection error"))
            rtt_process.kill()
        except:
            pass
    return

'''
        retrieves the throughput metrics from the server-side handler
'''
async def throughput_process(SERVER_IP, handler_port, throughput_port, recv_window, mtu, rtt, logger=FALLBACK_LOGGER):
    thpt_process = None
    try:
        client_params = {"MTU":mtu, "RTT":rtt, "SERVER_IP":SERVER_IP}
        websocket_url = "ws://"+SERVER_IP+":"+str(handler_port)
        async with websockets.connect(websocket_url) as websocket:
            await websocket.send(json.dumps(client_params))
            go_signal = await websocket.recv()
            thpt_process = subprocess.Popen(["iperf3",
                                           "--client", SERVER_IP, 
                                           "--time", "5",
                                           "--bandwidth", "100M",
                                           "--window", str(recv_window)+"K",
                                           "--reverse",
                                           "--port", str(throughput_port),
                                           "--format", "m"],
                                           stdout = subprocess.PIPE) 
            thpt_process.wait()
            await websocket.send("stop")
            thpt_results = await websocket.recv()
            thpt_results = json.loads(thpt_results)
            thpt_results["IDEAL_COMPUTED"] = recv_window * 8 / (float(rtt))/1000/1000
            try:
                thpt_process.kill()
            except:
                pass
            return thpt_results
    except:
        try:
            logger.error(("connection error"))
            thpt_process.kill()
        except:
            pass
    return

'''
        retrieves the throughput metrics from the server-side handler
'''
async def scan_process(**kwargs):
    scan_results = {"WINDOW_SCAN":[]}
    thpt_process = None
    try:
        for x in range(1,5):
            rwnd = kwargs["recv_window"]*1000*x*0.25
            modified_kwargs = {**kwargs}
            modified_kwargs["recv_window"] = rwnd
            thpt_results = await throughput_process(**modified_kwargs)
            scan_results["WINDOW_SCAN"].append(thpt_results)
        return scan_results
    except:
        try:
            logger.error(("connection error"))
            thpt_process.kill()
        except:
            pass
    return


'''
    REMOTE TO LOCAL PROTOCOL HANDLER
    serial steps are as follows (client side view):

        client                                                       server
                               < CONNECTION_ESTABLISHED >
                         ---- send arbitrary start message >>>>      <recv()>
        <recv()>         <<<<  send servers are up signal  ----      <send()>
        <mtu_test>             < CLIENT_PROCESSING_TIME >            <recv()>
        <rtt_test>             < CLIENT_PROCESSING_TIME >            <recv()>
        <BB_test>              < CLIENT_PROCESSING_TIME >            <recv()>
        <windows_scan>         < CLIENT_PROCESSING_TIME >            <recv()>
        <thpt_test>            < CLIENT_PROCESSING_TIME >            <recv()>
        <send()>         ----     send finish message      >>>>      <recv()>
        <analyzers>            < CLIENT_PROCESSING_TIME >            <done>
        <done>                    < OUTPUT_PRINTING >                <done>
                                < CONNECTION_TEARDOWN >
'''
async def reverse_client(logger, SERVER_IP):
    results = {}
    ws_url = "ws://"+SERVER_IP+":3001"
    client_ip = None
    mtu = None
    rtt = None
    bb = None
    rwnd = None
    logf = None
    try:
        logf = open(LOGFILE,"w+")
        client_ip = client_utils.get_client_ip()
    except:
        try:
            logger.error("cant get client ip")
            traceback.print_exc(file=logf)
        except:
            pass
        return

    try:
        mtu_return = await mtu_process(SERVER_IP, MTU_HANDLER_PORT, UDP_PORT, logger)
        results = {**results, **json.loads(mtu_return)}
    except:
        try:
            logger.error("mtu error")
            traceback.print_exc(file=logf)
        except:
            pass

    try:
        rtt_return = await rtt_process(SERVER_IP, RTT_HANDLER_PORT, logger)
        results = {**results, **json.loads(rtt_return)}
    except:
        try:
            logger.error("rtt error")
            traceback.print_exc(file=logf)
        except:
            pass

    try:
        rtt = results["RTT"]
        bb_return = await bandwidth_process(SERVER_IP, BANDWIDTH_HANDLER_PORT,, BANDWIDTH_SERVICE_PORT , rtt, logger)
        results = {**results, **json.loads(bb_return)}
    except:
        try:
            logger.error("bb error")
            traceback.print_exc(file=logf)
        except:
            pass

    try:
        mtu = results["MTU"]
        rwnd = results["RWND"]
        kwargs = {
                "SERVER_IP":SERVER_IP,
                "handler_port":THROUGHPUT_HANDLER_PORT,
                "throughput_port":THROUGHPUT_SERVICE_PORT,
                "recv_window":rwnd,
                "mtu":mtu,
                "rtt":rtt,
                "logger":logger
                }
        scan_return = await scan_process(**kwargs)
        results = {**results, **scan_return}
    except:
        try:
            logger.error("thpt error")
            traceback.print_exc(file=logf)
        except:
            pass

    return results

def start_reverse_test(server_ip):
    logger = getStreamLogger()
    try:
        client_utils.file_setter(LOGFILE)
        logger = getFileLogger(LOGFILE)
    except:
        pass

    ret_val = asyncio.get_event_loop().run_until_complete(reverse_client(logger, server_ip))
    return ret_val

if __name__ == "__main__":
    ret_val = start_reverse_test()
    print(ret_val)
