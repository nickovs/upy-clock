# Display a clock on a NeoPixel ring

# Pixel colour order is GBR

# NOTE: You need to insert your WiFi details in here
wifi_SSID = "EditMe"
wifi_PASSWORD = "MySecret"

# NOTE: It would be awfully nice of you to point this to a local NTP server
NTP_SERVER_NAME = "pool.ntp.org"

# NOTE: Edit this to indicate your offset from GMT, ignoring daylight savings
# NOTE: The daylight savings calculations are currently hardwired to the US model
#       (i.e. 2nd Sunday in March through to the 1st Sunday in November)
TIMEZONE = -7



import neoSPI
import network
import machine

import utime

import usocket
import ustruct
import gc


NTP_DELTA = 3155673600
tick_delta = utime.ticks_diff

# The clock in the ESP8266 controller is shockingly prone to drift, is
# often off by several percent and varies widely depending on
# temperature. This code attempts to compensate for bad local clocks.

class NTPClock:
    def __init__(self, host, history_length = 16):
        # Resolve server address in advance
        self.addr = usocket.getaddrinfo(host, 123)[0][-1]
        # Make and configure the client socket
        self.s = usocket.socket(usocket.AF_INET, usocket.SOCK_DGRAM)
        self.s.settimeout(1)
        # Get the time now
        ref = self.check_ntp_time()
        # Fill the history buffer
        self.history = [ref] * history_length
        self.h_head = 0
        # Until we have a couple of samples we can't calibrate the local clock
        self.rate = None
        # Wait long enough that we get samples far enough apart
        utime.sleep(5)
        # Try to get a second sample and set the clock rate
        err = None
        for i in range(5):
            try:
                self.update_ref_time()
                break
            except OSError as e:
                err = e
        if err:
            raise err
        
    def check_ntp_time(self):
        # A basic SNTP packet is 48 bytes
        NTP_QUERY = bytearray(48)
        # Set first byte to indicate an unsynchronised v3 client request
        NTP_QUERY[0] = 0x1b
        mt = utime.ticks_ms
        addr = self.addr
        sock = self.s
        send_fn = sock.sendto
        recv_fn = sock.recv
        t1 = mt()
        res = send_fn(NTP_QUERY, addr)
        msg = recv_fn(48)
        t2 = mt()
        val = ustruct.unpack_from("!Q", msg, 40)[0]
        # Arguably we should do some filtering here to deal with
        # requests that took a long time to come back. Maybe one day.
        return (val, t2)

    def update_ref_time(self):
        try:
            hh = self.history
            hh_len = len(hh)
            new_head = (self.h_head + 1) % hh_len
            hh[new_head] = self.check_ntp_time()
            self.h_head = new_head
            tail = (new_head + 1) % hh_len
            print("Head time: {}, tail time:{}".format(hh[new_head], hh[tail]))
            print("  ntp delta: {}, ms delta: {}".format((hh[new_head][0] - hh[tail][0]),
                                                         tick_delta(hh[tail][1], hh[new_head][1]) ))
            self.rate = (hh[new_head][0] - hh[tail][0]) // tick_delta(hh[tail][1], hh[new_head][1])
            print("Rate set to {}".format(self.rate))
            return True
        except OSError as e:
            if e.args[0] == 100:
                print("NTP check timed out")
            else:
                print("NTP check failed: {}".format(e))
            return False

    def time(self):
        ref_time, ref_ticks = self.history[self.h_head]
        # print("Ref time={}".format(ref_time, ref_ticks))
        ticks_now = utime.ticks_ms()
        # print("Ticks now: {}, delta {}".format(ticks_now, utime.ticks_diff(ref_ticks, ticks_now)))
        diff = tick_delta(ref_ticks, ticks_now)
        offset  = diff * self.rate
        # print("offset = {} ({})".format(offset, offset/(1<<32)))
        now = ref_time + offset
        return (now >> 32) - NTP_DELTA

# Time stamps of when DST starts and ends, either this year or next
# year if we are after the end of DST and have checked it since the
# change-over

DST_start = DST_end = 0

def DST_for_year(y):
    # This is hard-coded to the US model of starting on the second
    # Sunday in March and ending on the first Sunday in November
    
    mar_1_cal = utime.localtime(utime.mktime((y, 3, 1, 2, 0, 0, -1, -1)))
    start_time = utime.mktime((y, 3, 14 - mar_1_cal[6], 2, 0, 0, -1, -1))

    nov_1_cal = utime.localtime(utime.mktime((y, 11, 1, 2, 0, 0, -1, -1)))
    end_time = utime.mktime((y, 3, 7 - nov_1_cal[6], 2, 0, 0, -1, -1))

    return (start_time, end_time)
    
def is_DST(t):
    global DST_start, DST_end
    if DST_start == 0:
        DST_start, DST_end = DST_for_year(utime.localtime(t)[0])
    if t > DST_end:
        DST_start, DST_end = DST_for_year(utime.localtime(t)[0]+1)
    return t >= DST_start and t < DST_end

def fix_offset(t):
    off = TIMEZONE
    if is_DST(t):
        off += 1
    return off



class Clock:
    def __init__(self):
        sp = machine.SPI(1)
        sp.init(baudrate=3200000)
        self.np = neoSPI.NeoPixel(sp, 60)

    def display_tick(self,H,M,S):
        np = self.np
        if S == 0:
            for i in range(60):
                np.rotate(1)
                np.write()
        np[:] = (0,0,0)
        hh = H*5 + M//12
        np[hh%60, 1] = 200
        np[M,     0] = 150
        np[S,     2] = 175
        np.write()

def network_up():
    sta_if = network.WLAN(network.STA_IF)
    print("Checking for network")
    if not sta_if.active():
        print("Activating network")
        sta_if.active(True)

    if not sta_if.isconnected():
        print("connecting network")
        sta_if.connect(wifi_SSID, wifi_PASSWORD)
        for i in range(30):
            if sta_if.isconnected():
                print("Connected!")
                break
            utime.sleep(0.5)
    else:
        print("Network connected")

    print("Network config: {}".format(sta_if.ifconfig()))


def main():
    network_up()

    print("Fetching NTP time")
    nt = NTPClock(NTP_SERVER_NAME)

    print("Clock set to {}".format(utime.localtime(nt.time())))

    clock = Clock()

    # Initalise the tick time, time zone and DST
    last_tick = nt.time()
    offset = fix_offset(last_tick)

    # Loop forever
    while True:
        # Wait for the second to change over
        while True:
            utime.sleep_ms(10)
            now = nt.time()
            if now != last_tick:
                break
        _, _, _, H, M, S, _, _ = utime.localtime(now)

        H = (H + offset) % 24
        clock.display_tick(H,M,S)

        last_tick = now
            
        # Once a minute we check the drift on the NTP clock
        if S == 0:
            try:
                nt.update_ref_time()
            except OSError as e:
                if e.args[0] == 100:
                    print("NTP check timed out")
                else:
                    print("NTP check failed: {}".format(e))
        
        # Once an hour check we haven't started or ended daylight savings.
        # Doing this after we have ticked xx:59:59 ensures smooth ticks
        if S == 59 and M == 59:
            offset = fix_offset(now+1)

main()
