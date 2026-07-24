#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on April 29 8:39 PM 2024
Created in PyCharm
Created as Cosmic_Bench_DAQ_Control/hv_control.py

@author: Dylan Neff, Dylan
"""

import os
import threading
import time
import csv

from Server import Server
from caen_hv_py.CAENHVController import CAENHVController
from sim.fake_caen import FakeCAENHVController

# from run_config import Config


def get_hv_controller(hv_info):
    """Real CAEN controller, or the simulator when the run config asks for it."""
    ip_address = hv_info['ip']
    username = hv_info['username']
    password = hv_info['password']
    if hv_info.get('simulate') or ip_address == 'sim':
        return FakeCAENHVController(ip_address, username, password)
    return CAENHVController(ip_address, username, password)


def main():
    # config = Config()
    port = 1100
    monitor_stop_event, monitor_print_event, monitor_thread = threading.Event(), threading.Event(), None
    while True:
        try:
            with Server(port=port) as server:
                server.receive()
                server.send('HV control connected')
                hv_info = server.receive_json()

                caen_lock = threading.Lock()

                with get_hv_controller(hv_info) as caen_hv:
                    print('HV Connected')
                    res = server.receive()
                    while 'Finished' not in res:
                        if 'Start' in res:
                            monitor_print_event.clear()
                            server.send('HV ready to start')
                            sub_run = server.receive_json()
                            set_hvs(hv_info, sub_run['hvs'], caen_hv, caen_lock)
                            server.send(f'HV Set {sub_run["sub_run_name"]}')
                            monitor_print_event.set()
                        elif 'Power Off' in res:
                            server.send('HV ready to power off')
                            power_off_hvs(hv_info, caen_hv, caen_lock)
                            server.send('HV Powered Off')
                        elif 'Begin Monitoring' in res:
                            server.send('Starting HV monitor')
                            sub_run = server.receive_json()
                            monitor_stop_event.clear()
                            monitor_print_event.set()
                            monitor_args = (hv_info, sub_run['hvs'], sub_run['sub_run_name'],
                                            monitor_stop_event, monitor_print_event, caen_hv, caen_lock)
                            monitor_thread = threading.Thread(target=monitor_hvs, args=monitor_args)
                            monitor_thread.start()
                            server.send(f'HV monitoring started for {sub_run["sub_run_name"]}')
                        elif 'End Monitoring' in res:
                            server.send('Stopping HV monitor')
                            if monitor_thread is not None:
                                monitor_stop_event.set()
                                monitor_thread.join()
                                monitor_thread = None
                            server.send('HV Monitor Stopped')
                        else:
                            server.send('Unknown Command')
                        res = server.receive()
        except Exception as e:
            print(f'Error: {e}\nRestarting hv control server...')
            # Stop a running monitor thread before restarting: the CAEN session
            # (the `with get_hv_controller` block) has just closed, so the monitor
            # would otherwise keep polling a dead handle and spew errors.
            if monitor_thread is not None:
                monitor_stop_event.set()
                monitor_thread.join(timeout=15)
                monitor_thread = None
    print('donzo')


def set_hvs(hv_info, hvs, caen_hv, caen_lock):
    print('Setting HV...')
    with caen_lock:
        for slot, channel_v0s in hvs.items():
            for channel, v0 in channel_v0s.items():
                if v0 is None:  # Monitor only, skip setting
                    continue
                power = caen_hv.get_ch_power(int(slot), int(channel))
                if v0 == 0:  # If 0 V, turn off channel without setting voltage
                    if power:
                        caen_hv.set_ch_pw(int(slot), int(channel), 0)
                else:
                    caen_hv.set_ch_v0(int(slot), int(channel), v0)
                    if not power:
                        caen_hv.set_ch_pw(int(slot), int(channel), 1)

    # Bound the ramp wait. This loop used to have NO exit condition: a channel that
    # can never reach its setpoint — clamped by the crate's SVMax (as on 2026-07-24,
    # V0Set silently clamped 800->750), a dead channel, or a channel left powered
    # off — would spin here forever, blocking the whole run with no error. Now we
    # give up after ramp_timeout_s and raise, so daq_control/the operator see a loud
    # failure instead of a silent hang. HV is left where it is (not powered off);
    # the caller decides. Timeout is generous (real 0->900 V ramps take ~1-2 min).
    ramp_timeout_s = hv_info.get('ramp_timeout_s', 300)
    ramp_start = time.monotonic()
    all_ramped = False
    while not all_ramped:
        all_ramped = True
        stuck = []
        print('\nChecking HV ramp...')
        with caen_lock:
            for slot, channel_v0s in hvs.items():
                for channel, v0 in channel_v0s.items():
                    if v0 is None:
                        continue
                    vmon = caen_hv.get_ch_vmon(int(slot), int(channel))
                    if abs(vmon - v0) > 1.5:  # Make sure within 1.5 V of set value
                        all_ramped = False
                        stuck.append((slot, channel, vmon, v0))
                        print(f' Slot {slot}, Channel {channel} not ramped: {vmon:.2f} V --> {v0} V')
        if all_ramped:
            break
        if time.monotonic() - ramp_start > ramp_timeout_s:
            detail = ', '.join(f'{s}:{c} {vm:.1f}->{v0}V' for s, c, vm, v0 in stuck)
            raise RuntimeError(
                f'HV ramp timed out after {ramp_timeout_s}s; channels still off '
                f'setpoint: {detail}. Check the crate SVMax/limit on those channels '
                f'or a dead/off channel.')
        print('Waiting for HV to ramp...')
        time.sleep(10)  # lock released during sleep — monitor runs freely
    print('HV Ramped')


def power_off_hvs(hv_info, caen_hv, caen_lock):
    print('Powering off HV...')
    with caen_lock:
        for slot in range(hv_info['n_cards']):
            for channel in range(hv_info['n_channels_per_card']):
                power = caen_hv.get_ch_power(int(slot), int(channel))
                if power:
                    caen_hv.set_ch_pw(int(slot), int(channel), 0)
    print('HV Powered Off')


def monitor_hvs(hv_info, hvs, sub_run_name, stop_event, print_event, caen_hv, caen_lock):
    """
    Monitor the voltage and current of each HV channel and log the readings.
    Logs to CSV and prints human-readable output to the screen.
    """
    run_out_dir = hv_info['run_out_dir']
    monitor_interval = hv_info.get('monitor_interval', 10)  # seconds
    sub_run_out_dir = f'{run_out_dir}/{sub_run_name}'
    os.makedirs(sub_run_out_dir, exist_ok=True)
    log_file_path = f'{sub_run_out_dir}/hv_monitor.csv'

    # Build headers dynamically based on hvs dict
    headers = ["timestamp"]
    for slot, channel_v0s in hvs.items():
        for channel in channel_v0s.keys():
            prefix = f"{slot}:{channel}"
            headers.extend([f"{prefix} power", f"{prefix} v0", f"{prefix} vmon", f"{prefix} imon"])

    with open(log_file_path, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(headers)  # write headers once

        while not stop_event.is_set():
            row = [time.strftime("%Y-%m-%d %H:%M:%S")]

            with caen_lock:
                if print_event.is_set():
                    print(f"Monitoring HV \n{time.strftime('%H:%M:%S', time.strptime(row[0], '%Y-%m-%d %H:%M:%S'))}")
                for slot, channel_v0s in hvs.items():
                    for channel, v0 in channel_v0s.items():
                        power = caen_hv.get_ch_power(int(slot), int(channel))
                        vmon = caen_hv.get_ch_vmon(int(slot), int(channel))
                        imon = caen_hv.get_ch_imon(int(slot), int(channel))

                        row.extend([power, v0, vmon, imon])  # Append to row

                        if print_event.is_set():
                            v0_str = 'manual' if v0 is None else f'{v0:.2f}'
                            print(  # Human-readable output
                                f"Slot {slot} Channel {channel}: "
                                f"power={'on' if power else 'off'}, "
                                f"v set={v0_str}, v mon={vmon:.2f}, i mon={imon:.3f}"
                            )

            writer.writerow(row)
            csvfile.flush()  # ensure data is written to disk
            time.sleep(monitor_interval)  # lock released during sleep — set_hvs can run


if __name__ == '__main__':
    main()
