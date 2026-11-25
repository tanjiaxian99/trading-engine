#include <infiniband/verbs.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define NBUF 4096
#define BUFSZ 10240  // OCI runs 9000-byte jumbo frames (iSCSI root disk shares the port)
#define SNAP 128

static volatile int stop;
static void on_sig(int s) {
    (void)s;
    stop = 1;
}

int main(int argc, char** argv) {
    if (argc != 3) {
        fprintf(stderr, "usage: hwsniff <ibdev> <out.pcap>\n");
        return 2;
    }
    int n = 0;
    struct ibv_device** list = ibv_get_device_list(&n);
    struct ibv_context* ctx = NULL;
    for (int i = 0; i < n; i++)
        if (!strcmp(ibv_get_device_name(list[i]), argv[1]))
            ctx = ibv_open_device(list[i]);
    if (!ctx) {
        fprintf(stderr, "no device %s\n", argv[1]);
        return 1;
    }

    struct ibv_device_attr_ex dattr;
    if (ibv_query_device_ex(ctx, NULL, &dattr)) {
        perror("query_device_ex");
        return 1;
    }
    const double ns_per_tick = 1e6 / (double)dattr.hca_core_clock;  // hca_core_clock is in kHz
    fprintf(stderr, "hca_core_clock=%llu kHz\n", (unsigned long long)dattr.hca_core_clock);

    struct ibv_pd* pd = ibv_alloc_pd(ctx);
    struct ibv_cq_init_attr_ex cqa = {
        .cqe = NBUF, .wc_flags = IBV_WC_EX_WITH_BYTE_LEN | IBV_WC_EX_WITH_COMPLETION_TIMESTAMP};
    struct ibv_cq_ex* cq = ibv_create_cq_ex(ctx, &cqa);
    if (!cq) {
        perror("create_cq_ex (timestamps)");
        return 1;
    }

    struct ibv_qp_init_attr qpa = {
        .send_cq = ibv_cq_ex_to_cq(cq),
        .recv_cq = ibv_cq_ex_to_cq(cq),
        .cap = {.max_send_wr = 1, .max_recv_wr = NBUF, .max_send_sge = 1, .max_recv_sge = 1},
        .qp_type = IBV_QPT_RAW_PACKET};
    struct ibv_qp* qp = ibv_create_qp(pd, &qpa);
    if (!qp) {
        perror("create_qp raw (needs root)");
        return 1;
    }
    struct ibv_qp_attr a = {.qp_state = IBV_QPS_INIT, .port_num = 1};
    if (ibv_modify_qp(qp, &a, IBV_QP_STATE | IBV_QP_PORT)) {
        perror("qp init");
        return 1;
    }
    a.qp_state = IBV_QPS_RTR;
    if (ibv_modify_qp(qp, &a, IBV_QP_STATE)) {
        perror("qp rtr");
        return 1;
    }

    char* bufs = aligned_alloc(4096, (size_t)NBUF * BUFSZ);
    struct ibv_mr* mr = ibv_reg_mr(pd, bufs, (size_t)NBUF * BUFSZ, IBV_ACCESS_LOCAL_WRITE);
    for (int i = 0; i < NBUF; i++) {
        struct ibv_sge sge = {
            .addr = (uintptr_t)(bufs + (size_t)i * BUFSZ), .length = BUFSZ, .lkey = mr->lkey};
        struct ibv_recv_wr wr = {.wr_id = (uint64_t)i, .sg_list = &sge, .num_sge = 1}, *bad;
        if (ibv_post_recv(qp, &wr, &bad)) {
            perror("post_recv");
            return 1;
        }
    }
    struct ibv_flow_attr fa = {
        .type = IBV_FLOW_ATTR_SNIFFER, .size = sizeof(fa), .num_of_specs = 0, .port = 1};
    if (!ibv_create_flow(qp, &fa)) {
        perror("create_flow sniffer");
        return 1;
    }

    FILE* out = fopen(argv[2], "wb");
    const uint32_t hdr[6] = {0xa1b23c4d, 0x00040002, 0, 0, SNAP, 1};  // nanosecond pcap, Ethernet
    fwrite(hdr, 4, 6, out);
    signal(SIGINT, on_sig);
    signal(SIGTERM, on_sig);
    fprintf(stderr, "sniffing %s\n", argv[1]);

    uint64_t count = 0, errors = 0;
    struct ibv_poll_cq_attr pa = {0};
    while (!stop) {
        if (ibv_start_poll(cq, &pa))
            continue;
        do {
            const uint64_t i = cq->wr_id;
            if (cq->status == IBV_WC_SUCCESS) {
                const uint32_t len = ibv_wc_read_byte_len(cq);
                const uint64_t ns = (uint64_t)((double)ibv_wc_read_completion_ts(cq) * ns_per_tick);
                const uint32_t cap = len < SNAP ? len : SNAP;
                const uint32_t rec[4] = {(uint32_t)(ns / 1000000000ULL),
                                         (uint32_t)(ns % 1000000000ULL), cap, len};
                fwrite(rec, 4, 4, out);
                fwrite(bufs + i * BUFSZ, 1, cap, out);
                count++;
            } else {
                errors++;
            }
            struct ibv_sge sge = {
                .addr = (uintptr_t)(bufs + i * BUFSZ), .length = BUFSZ, .lkey = mr->lkey};
            struct ibv_recv_wr wr = {.wr_id = i, .sg_list = &sge, .num_sge = 1}, *bad;
            ibv_post_recv(qp, &wr, &bad);
        } while (!ibv_next_poll(cq));
        ibv_end_poll(cq);
    }
    fclose(out);
    fprintf(stderr, "%llu packets, %llu completion errors\n", (unsigned long long)count,
            (unsigned long long)errors);
    struct ibv_qp_attr qa;
    struct ibv_qp_init_attr qi;
    if (!ibv_query_qp(qp, &qa, IBV_QP_STATE, &qi) && qa.qp_state == IBV_QPS_ERR)
        fprintf(stderr, "WARNING: sniffer QP went to error state, capture is incomplete\n");
    return 0;
}
