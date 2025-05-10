import csv
import os
import time
from operator import attrgetter

from ryu.app import simple_switch_13
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.lib import hub


class SimpleMonitorCSV(simple_switch_13.SimpleSwitch13):

    def __init__(self, *args, **kwargs):
        super(SimpleMonitorCSV, self).__init__(*args, **kwargs)
        self.datapaths = {}
        self.monitor_thread = hub.spawn(self._monitor)

        # Tạo thư mục lưu CSV nếu chưa có
        self.csv_dir = "SDN/web/data"
        os.makedirs(self.csv_dir, exist_ok=True)

    def _write_csv(self, filepath, header, rows):
        write_header = not os.path.exists(filepath)
        with open(filepath, 'a', newline='') as f:
            writer = csv.writer(f)
            if write_header:
                writer.writerow(header)
            for row in rows:
                writer.writerow(row)

    @set_ev_cls(ofp_event.EventOFPStateChange,
                [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change_handler(self, ev):
        datapath = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            if datapath.id not in self.datapaths:
                self.logger.debug('Register datapath: %016x', datapath.id)
                self.datapaths[datapath.id] = datapath
        elif ev.state == DEAD_DISPATCHER:
            if datapath.id in self.datapaths:
                self.logger.debug('Unregister datapath: %016x', datapath.id)
                del self.datapaths[datapath.id]

    def _monitor(self):
        while True:
            for dp in self.datapaths.values():
                self._request_stats(dp)
            hub.sleep(10)

    def _request_stats(self, datapath):
        self.logger.debug('Send stats request: %016x', datapath.id)
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        datapath.send_msg(parser.OFPFlowStatsRequest(datapath))
        datapath.send_msg(parser.OFPPortStatsRequest(datapath, 0, ofproto.OFPP_ANY))
        datapath.send_msg(parser.OFPTableStatsRequest(datapath))
        datapath.send_msg(parser.OFPDescStatsRequest(datapath))
        datapath.send_msg(parser.OFPGroupStatsRequest(datapath))
        datapath.send_msg(parser.OFPQueueStatsRequest(datapath, 0, ofproto.OFPP_ANY, ofproto.OFPQ_ALL))
        datapath.send_msg(parser.OFPMeterStatsRequest(datapath, 0xffff))

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def _flow_stats_reply_handler(self, ev):
        body = ev.msg.body
        dpid = ev.msg.datapath.id
        timestamp = time.time()

        header = ["timestamp", "dpid", "in_port", "eth_dst", "out_port", "packet_count", "byte_count", "duration_sec"]
        rows = []
        for stat in body:
            match = stat.match
            in_port = match.get('in_port', '-')
            eth_dst = match.get('eth_dst', '-')
            out_port = stat.instructions[0].actions[0].port if stat.instructions else '-'
            rows.append([timestamp, dpid, in_port, eth_dst, out_port,
                         stat.packet_count, stat.byte_count, stat.duration_sec])

        self._write_csv(os.path.join(self.csv_dir, f"flow_stats_{dpid}.csv"), header, rows)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats_reply_handler(self, ev):
        body = ev.msg.body
        dpid = ev.msg.datapath.id
        timestamp = time.time()

        header = ["timestamp", "dpid", "port_no", "rx_packets", "rx_bytes", "rx_errors", "tx_packets", "tx_bytes", "tx_errors"]
        rows = []
        for stat in sorted(body, key=attrgetter('port_no')):
            rows.append([timestamp, dpid, stat.port_no,
                         stat.rx_packets, stat.rx_bytes, stat.rx_errors,
                         stat.tx_packets, stat.tx_bytes, stat.tx_errors])

        self._write_csv(os.path.join(self.csv_dir, f"port_stats_{dpid}.csv"), header, rows)

    @set_ev_cls(ofp_event.EventOFPTableStatsReply, MAIN_DISPATCHER)
    def _table_stats_reply_handler(self, ev):
        body = ev.msg.body
        dpid = ev.msg.datapath.id
        timestamp = time.time()

        header = ["timestamp", "dpid", "table_id", "active_count", "lookup_count", "matched_count"]
        rows = []
        for stat in body:
            rows.append([timestamp, dpid, stat.table_id,
                         stat.active_count, stat.lookup_count, stat.matched_count])

        self._write_csv(os.path.join(self.csv_dir, f"table_stats_{dpid}.csv"), header, rows)

    @set_ev_cls(ofp_event.EventOFPDescStatsReply, MAIN_DISPATCHER)
    def _desc_stats_reply_handler(self, ev):
        stat = ev.msg
        dpid = stat.datapath.id
        timestamp = time.time()

        desc = stat.body
        header = ["timestamp", "dpid", "mfr_desc", "hw_desc", "sw_desc", "serial_num", "dp_desc"]
        row = [timestamp, dpid, desc.mfr_desc, desc.hw_desc, desc.sw_desc, desc.serial_num, desc.dp_desc]

        self._write_csv(os.path.join(self.csv_dir, f"desc_stats_{dpid}.csv"), header, [row])

    @set_ev_cls(ofp_event.EventOFPGroupStatsReply, MAIN_DISPATCHER)
    def _group_stats_reply_handler(self, ev):
        body = ev.msg.body
        dpid = ev.msg.datapath.id
        timestamp = time.time()

        header = ["timestamp", "dpid", "group_id", "ref_count", "packet_count", "byte_count", "duration_sec"]
        rows = []
        for stat in body:
            rows.append([timestamp, dpid, stat.group_id, stat.ref_count,
                         stat.packet_count, stat.byte_count, stat.duration_sec])

        self._write_csv(os.path.join(self.csv_dir, f"group_stats_{dpid}.csv"), header, rows)

    @set_ev_cls(ofp_event.EventOFPQueueStatsReply, MAIN_DISPATCHER)
    def _queue_stats_reply_handler(self, ev):
        body = ev.msg.body
        dpid = ev.msg.datapath.id
        timestamp = time.time()

        header = ["timestamp", "dpid", "port_no", "queue_id", "tx_bytes", "tx_packets", "tx_errors"]
        rows = []
        for stat in body:
            rows.append([timestamp, dpid, stat.port_no, stat.queue_id,
                         stat.tx_bytes, stat.tx_packets, stat.tx_errors])

        self._write_csv(os.path.join(self.csv_dir, f"queue_stats_{dpid}.csv"), header, rows)

    @set_ev_cls(ofp_event.EventOFPMeterStatsReply, MAIN_DISPATCHER)
    def _meter_stats_reply_handler(self, ev):
        body = ev.msg.body
        dpid = ev.msg.datapath.id
        timestamp = time.time()

        header = ["timestamp", "dpid", "meter_id", "flow_count", "packet_in_count", "byte_in_count", "duration_sec"]
        rows = []
        for stat in body:
            rows.append([timestamp, dpid, stat.meter_id,
                         stat.flow_count, stat.packet_in_count,
                         stat.byte_in_count, stat.duration_sec])

        self._write_csv(os.path.join(self.csv_dir, f"meter_stats_{dpid}.csv"), header, rows)
