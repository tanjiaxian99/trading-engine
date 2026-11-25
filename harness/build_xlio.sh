#!/usr/bin/env bash
# Usage: harness/build_xlio.sh
# Builds NVIDIA XLIO (kernel bypass for ConnectX NICs) and its libdpcp dependency into /opt/xlio
set -euo pipefail

xlio_version=3.61.2
src_dir="$HOME/xlio_src"

sudo apt-get install -y -qq git autoconf automake libtool unzip patch pkg-config libnl-3-dev \
    libnl-route-3-dev libibverbs-dev librdmacm-dev rdma-core ibverbs-providers ibverbs-utils

mkdir -p "$src_dir"
cd "$src_dir"
[[ -d libdpcp ]] || git clone -q --depth 1 https://github.com/Mellanox/libdpcp.git
[[ -d libxlio ]] || git clone -q --depth 1 --branch "$xlio_version" https://github.com/Mellanox/libxlio.git

(cd libdpcp && ./autogen.sh && ./configure -q --prefix=/opt/xlio && make -s -j"$(nproc)" &&
    sudo make -s install)
(cd libxlio && ./autogen.sh && ./configure -q --prefix=/opt/xlio --with-dpcp=/opt/xlio &&
    make -s -j"$(nproc)" && sudo make -s install)
ls /opt/xlio/lib/libxlio.so
