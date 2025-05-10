from mininet.net import Mininet
from mininet.topo import Topo
from mininet.link import Link
from mininet.node import RemoteController
from mininet.log import setLogLevel
import time
import random
import os
import threading
import subprocess
import signal
from datetime import datetime
import sflow

class MultiSwitchTopo(Topo):
    def build(self):
        # Tầng core: 1 switch trung tâm
        core = self.addSwitch('s1')

        # Tầng distribution: 3 switch
        dist_switches = []
        for i in range(2, 5):  # s2, s3, s4
            sw = self.addSwitch(f's{i}')
            dist_switches.append(sw)
            # Link core → distribution (WAN-like)
            self.addLink(core, sw, cls=Link)

        host_id = 1
        # Tầng access: Mỗi distribution switch nối với 3 access switch
        for dist in dist_switches:
            for i in range(3):  # 3 access switch per distribution
                sw = self.addSwitch(f's{len(dist_switches) * 3 + i + 2}')
                # Link distribution → access (LAN-like)
                self.addLink(dist, sw, cls=Link)

                # Mỗi access switch có 10 host
                for j in range(10):
                    host = self.addHost(f'h{host_id}')
                    self.addLink(sw, host, cls=Link)
                    host_id += 1

def configure_links(net):
    """Cấu hình tất cả các link với tc và quantum cố định."""
    for link in net.links:
        intf1 = link.intf1
        intf2 = link.intf2
        intf1_name = intf1.name
        intf2_name = intf2.name
        # Các tham số cho từng loại link
        if 's1' in intf1.node.name or 's1' in intf2.node.name:  # Core -> Distribution (WAN-like)
            bw = random.uniform(50, 100)
            delay = '20ms'
            jitter = '1ms'
            loss = 2.0  # Tăng tỷ lệ mất gói
        elif 'h' in intf1.node.name or 'h' in intf2.node.name:  # Access -> Host
            bw = max(50, random.uniform(10, 50))
            delay = '1ms'
            jitter = '0.1ms'
            loss = 5.0  # Tăng tỷ lệ mất gói
        else:  # Distribution -> Access (LAN-like)
            bw = random.uniform(100, 500)
            delay = '2ms'
            jitter = '0.2ms'
            loss = 1.0  # Tăng tỷ lệ mất gói

        quantum = 1500  # Đặt quantum cố định
        # Xóa cấu hình cũ (nếu có)
        intf1.node.cmd(f'tc qdisc del dev {intf1_name} root 2>/dev/null')
        intf2.node.cmd(f'tc qdisc del dev {intf2_name} root 2>/dev/null')
        # Áp dụng cấu hình mới với quantum cố định
        for intf in [intf1, intf2]:
            intf_name = intf.name
            intf.node.cmd(f'tc qdisc add dev {intf_name} root handle 1: htb default 10')
            intf.node.cmd(f'tc class add dev {intf_name} parent 1: classid 1:10 htb rate {bw}mbit quantum {quantum}')
            intf.node.cmd(f'tc qdisc add dev {intf_name} parent 1:10 netem delay {delay} jitter {jitter} loss {loss}%')

def run_traffic(net, iteration, duration=600):
    hosts = net.hosts
    log_dir = "logs"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # Dừng các tiến trình iperf server cũ
    for host in hosts:
        host.cmd('pkill -f "iperf -s"')

    start_time = time.time()
    print(f"🔄 Starting traffic simulation (iteration {iteration})...")

    # Mô hình lưu lượng theo chu kỳ (cao điểm/thấp điểm)
    def get_traffic_intensity():
        elapsed = (time.time() - start_time) % 60  # Chu kỳ 60 giây
        intensity = 0.5 + 0.5 * (1 + math.sin(2 * math.pi * elapsed / 60))
        return intensity

    # Chạy các tác vụ lưu lượng song song
    threads = []
    active_servers = []

    # Cài đặt iperf server
    def start_iperf_server(host):
        host.cmd(f'iperf -s -D > {log_dir}/{host.name}_iperf_server.log')
        active_servers.append(host)

    for server in random.sample(hosts, 3):
        t = threading.Thread(target=start_iperf_server, args=(server,))
        t.start()
        threads.append(t)
        time.sleep(0.1)

    # Ping ngẫu nhiên
    def run_ping(src, dst):
        src.cmd(f'ping -c 100 {dst.IP()} > {log_dir}/{src.name}_to_{dst.name}_ping.log &')  # Tăng số gói tin

    # Iperf client (TCP)
    def run_iperf(client, server):
        client.cmd(f'iperf -c {server.IP()} -t 5 > {log_dir}/{client.name}_to_{server.IP()}_iperf.log &')

    # HTTP traffic (giả lập với máy chủ web đơn giản)
    def run_http(src, dst):
        dst.cmd(f'python3 -m http.server 8000 --bind {dst.IP()} &')
        time.sleep(0.5)
        src.cmd(f'curl -s http://{dst.IP()}:8000 > /dev/null &')
        time.sleep(1)
        dst.cmd('pkill -f "http.server"')

    # Video streaming (giả lập với netcat)
    def run_video(src, dst):
        dst.cmd(f'nc -l 12345 > /dev/null &')
        time.sleep(0.5)
        src.cmd(f'dd if=/dev/zero bs=1M count=10 | nc {dst.IP()} 12345 &')

    # VoIP (iperf UDP để ghi nhận mất gói)
    def run_voip(src, dst):
        dst.cmd(f'iperf -s -u -D > {log_dir}/{dst.name}_voip_server.log')
        time.sleep(0.5)
        src.cmd(f'iperf -c {dst.IP()} -u -b 64k -t 5 --reportstyle C > {log_dir}/{src.name}_to_{dst.IP()}_voip.log &')

    # Mô phỏng lưu lượng
    while time.time() - start_time < duration:
        intensity = get_traffic_intensity()
        num_tasks = int(50 * intensity)  # Tăng số lượng tác vụ

        for _ in range(num_tasks):
            src, dst = random.sample(hosts, 2)
            if src == dst:
                continue

            # task_type = random.choice(['ping', 'iperf', 'http', 'video', 'voip'])
            # print(f"{src.name} ➡ {dst.name} ({task_type})")
            # if task_type == 'ping':
            #     t = threading.Thread(target=run_ping, args=(src, dst))
            # elif task_type == 'iperf':
            #     server = random.choice(active_servers)
            #     t = threading.Thread(target=run_iperf, args=(src, server))
            # elif task_type == 'http':
            #     t = threading.Thread(target=run_http, args=(src, dst))
            # elif task_type == 'video':
            #     t = threading.Thread(target=run_video, args=(src, dst))
            # elif task_type == 'voip':
            #     t = threading.Thread(target=run_voip, args=(src, dst))
            t = threading.Thread(target=run_ping, args=(src, dst))
            t.start()
            threads.append(t)
            time.sleep(random.uniform(0.1, 0.5))

        time.sleep(5)

    # Dọn dẹp
    for host in hosts:
        host.cmd('pkill -f "iperf -s"')
        host.cmd('pkill -f "http.server"')
        host.cmd('pkill -f "nc -l"')
    for t in threads:
        t.join()

def analyze_logs():
    log_dir = "logs"
    ping_delays = []
    iperf_throughputs = []
    packet_loss_rates = []

    for log_file in os.listdir(log_dir):
        if 'ping.log' in log_file:
            with open(os.path.join(log_dir, log_file), 'r') as f:
                for line in f:
                    if 'time=' in line:
                        delay = float(line.split('time=')[1].split(' ')[0])
                        ping_delays.append(delay)
                    # Kiểm tra packet loss trong summary của ping
                    if 'packet loss' in line:
                        loss_str = line.split('packet loss')[0].split()[-1].strip('%')
                        if loss_str:
                            loss = float(loss_str)
                            packet_loss_rates.append(loss)
        elif 'iperf.log' in log_file and 'server' not in log_file:
            with open(os.path.join(log_dir, log_file), 'r') as f:
                for line in f:
                    if 'Mbits/sec' in line:
                        throughput = float(line.split('Mbits/sec')[0].split()[-1])
                        iperf_throughputs.append(throughput)
        elif 'voip.log' in log_file and 'server' not in log_file:
            with open(os.path.join(log_dir, log_file), 'r') as f:
                for line in f:
                    # Định dạng CSV của iperf UDP: ...,sent,loss,loss%,...
                    if ',' in line:
                        parts = line.strip().split(',')
                        if len(parts) > 10 and parts[9]:  # loss% ở cột 10
                            loss = float(parts[9])
                            packet_loss_rates.append(loss)

    avg_delay = sum(ping_delays) / len(ping_delays) if ping_delays else 0
    avg_throughput = sum(iperf_throughputs) / len(iperf_throughputs) if iperf_throughputs else 0
    avg_packet_loss = sum(packet_loss_rates) / len(packet_loss_rates) if packet_loss_rates else 0
    print(f"📊 Analysis: Average ping delay: {avg_delay:.2f} ms, Average throughput: {avg_throughput:.2f} Mbps, Average packet loss: {avg_packet_loss:.2f}%")

if __name__ == '__main__':
    import math
    setLogLevel('info')
    topo = MultiSwitchTopo()
    net = Mininet(topo=topo, link=Link, controller=RemoteController, autoSetMacs=True)
    net.start()
    time.sleep(3)

    # Cấu hình tất cả các link với tc và quantum cố định
    print("🔧 Configuring links with custom tc settings...")
    configure_links(net)

    # Số lượng iteration mong muốn
    num_iterations = 3
    for iteration in range(1, num_iterations + 1):
        print(f"🚀 Starting iteration {iteration}/{num_iterations}")
        run_traffic(net, iteration=iteration, duration=600)
        analyze_logs()
        print(f"✅ Completed iteration {iteration}/{num_iterations}\n")

    net.stop()
    print("✅ Simulation completed.")