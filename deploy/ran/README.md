# RAN Node Deployment

> Stub — this file will be fleshed out into a full node deployment guide in a
> later cleanup task. For now it preserves reproducibility-relevant tables
> extracted from `docs/BAB3_CODE_SNIPPETS.md` before that file was removed
> from the public tree (it was a thesis-drafting source-of-truth appendix,
> not itself reproducibility documentation).

## Interfaces

### Antarmuka & port

| Antarmuka | Transport | Arah | Endpoint | Port | Sumber asli |
|---|---|---|---|---|---|
| **E2AP** | SCTP | gNB (DU) → RIC | `10.91.2.2` | `36421` | `cots_n78_copied.yml` (`e2.addr/port`) |
| **E42** | TCP | xApp → RIC | `10.91.2.2` | `36422` | `my_xapp_kpm.conf` (`NearRT_RIC_IP`, `E42_Port`) |
| **N2 (NGAP)** | SCTP | gNB → AMF | `10.91.2.4` | `38412` | `cots_n78_copied.yml` (`amf.addr/port`), `amf.yaml` (`ngap`) |
| **N3 (GTP-U)** | UDP | gNB → UPF | `10.91.2.4` | `2152` | `upf.yaml` (`gtpu.address`) |
| **SBI** | HTTP | internal 5GC | `127.0.0.x` | `7777` | `smf.yaml`/`amf.yaml` (loopback) |

### Alamat IP node

| Node | Peran | IP (`10.91.2.0/24`) | Software |
|---|---|---|---|
| RAN | gNodeB + E2 Agent (srsRAN) | `10.91.2.1` | srsRAN Project |
| RIC | Near-RT RIC + xApp + Exporter + Grafana | `10.91.2.2` | FlexRIC |
| Core | 5GC (AMF/SMF/UPF) | `10.91.2.4` | Open5GS |
| UE | Terminal pengguna | `10.45.0.0/16` (via DHCP/SMF) | Oppo Reno 8 5G, Motorola G35 5G |

## gNB config fields

### Identitas PLMN & slice (`cots_n78_copied.yml`)

| Parameter | Nilai | Sumber asli |
|---|---|---|
| PLMN ID | `00101` (MCC `001` / MNC `01`) | `cots_n78_copied.yml` (`plmn`), `amf.yaml` (`guami`) |
| TAC | `7` | `cots_n78_copied.yml` (`tac`), `amf.yaml` |
| S-NSSAI | SST `1` (eMBB) | `amf.yaml` (`plmn_support`), `xapp_sec_mitigate.c` (`--sst 1`) |
| PCI | `1` | `cots_n78_copied.yml` (`pci`) |
| Nama AMF | `open5gs-amf0` | `amf.yaml` (`amf_name`) |
