# Experimento EDoS sobre o LST 2.0

Topologia e ferramental para emular e estudar ataques **EDoS (Economic Denial of
Sustainability)** em ambientes de nuvem elástica, construída sobre o
[Lightweight SDN Testbed (LST) 2.0](../README.md).

> Trabalho de Conclusão de Curso — **mrkeepout** e **Matheus (matw4b)**.
> Fork de [alexandrekaihara/lst2.0](https://github.com/alexandrekaihara/lst2.0).

## O que é EDoS (e por que não é só DoS)

Um DoS/DDoS clássico busca **indisponibilizar** o serviço. Já o **EDoS** explora a
**elasticidade** da nuvem: o atacante gera carga que força o *auto-scaling* a
provisionar mais recursos, **inflando o custo** da vítima — muitas vezes **sem
derrubar** o serviço. O dano é econômico, não de disponibilidade.

Para que o cenário seja EDoS de verdade, o testbed precisa de dois ingredientes que
o LST não tinha nativamente e que foram implementados aqui:

1. **Elasticidade** — um autoscaler que provisiona/remove réplicas sob demanda.
2. **Medição de custo** — contabilização de *réplica-segundos* (a "conta" da nuvem).

## Arquitetura

```
                        c1 (controlador Ryu / SDN)
                          |
   atacantes (a1..aN) --\ |
   clientes    (l1..lM) --\|
                        [ br0 ]   switch OpenvSwitch (captura sFlow/pcap)
                          |
                        lb (VIP :80)   proxy round-robin, backends dinâmicos
                          |
                w1  w2  ... wK    pool web (Apache:80), escalado MIN..MAX
                          ^
   autoscaler (host) -> conta conexões ativas no lb (ss) -> scale up/down
                     -> reescreve backends do lb  -> loga custo (réplica-s) em CSV
   atacantes -> slowhttptest (Slowloris/Slow POST/Slow Read) contra o VIP do lb
```

- **`br0`** — switch SDN (OVS) conectado a um controlador **Ryu** (`c1`); ponto de
  captura de fluxos.
- **`lb`** — *load balancer* (VIP único `192.168.100.10:80`) que distribui as
  requisições entre as réplicas ativas por *round-robin*. Relê a lista de backends
  **a cada nova conexão**, então o autoscaler só precisa reescrever um arquivo.
- **`w1..wK`** — pool de servidores web (imagem `:web`, Apache na porta 80) que o
  autoscaler escala.
- **`l1..lM`** — clientes legítimos (tráfego HTTP benigno de fundo).
- **`a1..aN`** — atacantes, dirigindo o **slowhttptest**.
- **autoscaler** — processo no host que mede a carga e escala o pool.

## Por que o sinal de scaling é "conexões", não CPU

O motor de ataque é o **slowhttptest** (*low-and-slow*: Slowloris, Slow POST, Slow
Read). Esse tipo de ataque **satura a concorrência/conexões** do servidor segurando
sockets abertos — **sem consumir CPU**. Por isso o autoscaler escala pelo **número
de conexões ativas medidas no `lb`** (`ss ... state established`), que é o gatilho
fiel a *load balancers*/autoscalers de nuvem e o único que reage bem a ataques slow.

## Modos de ataque

Selecionáveis em `edos_config.py` via `ATTACK_MODE`:

- **`sustained`** — mantém o slowhttptest rodando continuamente; segura a carga acima
  do limiar e **mantém o serviço no máximo de réplicas** o tempo todo.
- **`yoyo`** — pulsa `PULSE_ON` s de ataque e `PULSE_OFF` s de silêncio, repetidamente,
  forçando **ciclos de scale-up/scale-down** e maximizando o desperdício econômico.
  É o padrão EDoS clássico da literatura.

## Arquivos

| Arquivo | Papel |
|---|---|
| [`edos.py`](edos.py) | Orquestrador principal: monta a topologia, inicia captura, autoscaler e ataques; no `CTRL+C` faz teardown + relatórios |
| [`edos_config.py`](edos_config.py) | Todos os parâmetros (IPs, limiares de scaling, modo/força de ataque, billing) |
| [`edos_autoscaler.py`](edos_autoscaler.py) | Autoscaler elástico com ciclo de vida realista (warmup → active → draining), quantum mínimo de cobrança e custo LCU (compute + ALB + banda) |
| [`edos_labeling.py`](edos_labeling.py) | Dedup da captura (borda cliente↔LB) + rotulagem bidirecional por faixa de IP |
| [`loadbalancer/lb.py`](loadbalancer/lb.py) | Proxy TCP round-robin com backends recarregados dinamicamente |
| [`edos_attack/edos_attacker.py`](edos_attack/edos_attacker.py) | Wrapper do slowhttptest (modos `sustained`/`yoyo`) |
| [`client_profiles.py`](client_profiles.py) | Framework de clientes benignos configuráveis (perfis `browsing`/`mailing`/`printing` no motor SURGE) |
| [`legit_client.py`](legit_client.py) | Gerador benigno ingênuo (single-rate) — **substituído** por `client_profiles.py`, mantido só para referência |

## Requisitos

- **Linux com Docker** e **privilégios de root** (o LST cria *network namespaces*,
  interfaces veth e regras `iptables`).
- Python 3.8+.
- Acesso à Internet na primeira execução (para baixar as imagens Docker e para os
  atacantes instalarem o `slowhttptest` em runtime).

> Não roda sem root. Em WSL2 funciona desde que o Docker esteja ativo.

## Como executar

```bash
cd demonstration
sudo python3 edos.py
```

Na primeira vez, a instanciação demora mais porque as imagens são baixadas do Docker
Hub. Para encerrar, pressione **CTRL+C uma vez** — o experimento derruba todos os
containers, gera o relatório de fluxos e imprime o resumo de custo.

## Parâmetros principais (`edos_config.py`)

| Parâmetro | Significado |
|---|---|
| `MIN_REPLICAS` / `MAX_REPLICAS` | Limites do pool web |
| `SCALE_UP_CONN` / `SCALE_DOWN_CONN` | Limiares de conexões **por réplica** para escalar |
| `TICK_SECONDS` | Intervalo de amostragem/decisão do autoscaler |
| `COOLDOWN_SECONDS` | Tempo mínimo entre duas ações de scaling |
| `WARMUP_SECONDS` | Tempo que uma réplica nova fica **cobrada mas fora do pool** (janela que o yo-yo explora) |
| `DRAIN_TIMEOUT_SECONDS` | Janela de *connection draining* no scale-down antes de remover a réplica |
| `PRICING` | Perfil de preços plugável (AWS): custo de VM, LCU da ALB (conexões ativas/novas), banda e **quantum mínimo de cobrança** |
| `COST_PER_REPLICA_SEC` | *(legado)* constante antiga mantida só para referência; o custo real vem de `PRICING` |
| `ATTACK_MODE` | `sustained` ou `yoyo` |
| `SLOW_TEST` | `slowloris` / `slowpost` / `slowread` |
| `SLOW_CONNS` / `SLOW_RATE` / `SLOW_INTERVAL` | Força do slowhttptest (`-c` / `-r` / `-i`) |
| `PULSE_ON` / `PULSE_OFF` | Duração dos pulsos no modo `yoyo` |
| `FLEET` | Frota benigna declarativa: lista de `{profile, count}` (quantos e de qual tipo) |
| `SEED` | Semente única do run, propagada aos clientes (reprodutibilidade) |
| `NUM_CLIENTS` / `NUM_ATTACKERS` | Nº de atacantes; `NUM_CLIENTS` é fallback se `FLEET` estiver vazia |
| `PCAP_ROTATE_SECONDS` | Intervalo de rotação do pcap (maior ⇒ corta menos conexões slow) |
| `HEALTHCHECK_RETRIES` / `HEALTHCHECK_INTERVAL` | Tentativas/espera do health-check do VIP antes de medir |

Sugestão para iterar rápido: `MIN=1, MAX=3, TICK=5s, PULSE_ON=20s, PULSE_OFF=20s,
SLOW_CONNS≈200, SCALE_UP_CONN≈50/réplica`. Ajuste os limiares à capacidade da máquina
(o `MaxClients` do Apache define quantas conexões cada réplica aguenta).

## Saídas geradas

- **`flows/edos_cost.csv`** — linha do tempo por tick. Colunas: `timestamp,
  active_replicas` (réplicas **cobradas** = warming+active+draining)`, active_conns,
  cumulative_cost` (compatíveis com o `plot_edos.py`), mais as colunas da Fase 2:
  `warming, active, draining, new_conns_per_s, bytes_per_s, vm_cost, lcu_cost,
  bw_cost`. É a evidência quantitativa do EDoS.
- **Resumo de custo** (impresso no `CTRL+C`) — eventos de scale-up/down,
  réplica-segundos (incluindo os cobrados pelo quantum mínimo) e o custo decomposto
  em **compute + LCU da ALB + banda**.
- **`flows/final_report.csv`** — estatísticas de fluxo geradas pelo
  [CICFlowMeter](https://www.unb.ca/cic/research/applications.html), já
  **deduplicadas** (só a borda cliente↔LB; o hop interno LB↔réplica é descartado)
  e **rotuladas** na coluna `Label` por faixa de IP (`attack` p/ `.71–.79`,
  `benign` p/ `.51–.59`). O rótulo checa **os dois extremos** do fluxo, então uma
  conexão slow cortada pela rotação do pcap (direção invertida pelo CICFlowMeter)
  ainda é rotulada corretamente. Ver [`edos_labeling.py`](edos_labeling.py).
- **`flows/run_manifest.json`** — manifesto do run (git SHA, timestamp, semente,
  duração, frota expandida e a configuração efetiva), para o dataset ser
  regenerável e permitir o split Train/Test por run.

## Como verificar que está funcionando

1. **Conectividade** — `docker exec l1 wget -qO- http://192.168.100.10` retorna a
   página do Apache (cliente → LB → réplica).
2. **Sinal de carga** — com o ataque ativo,
   `docker exec lb ss -Hnt state established '( sport = :80 )' | wc -l` sobe.
3. **Elasticidade** (`sustained`) — no `edos_cost.csv`, as réplicas sobem de `MIN` até
   `MAX`; parando o ataque, voltam a `MIN`.
4. **Yo-yo** — no `edos_cost.csv`, as réplicas **oscilam** em fase com
   `PULSE_ON`/`PULSE_OFF` (vários ciclos scale-up/down).
5. **Teardown** — após `CTRL+C`, `docker ps` não lista nenhum container do
   experimento (`br0`, `c1`, `lb`, `w*`, `a*`, `l*`).

## Aviso de uso

Este ferramental é para **pesquisa acadêmica em ambiente controlado e isolado**
(o testbed roda inteiramente em containers locais). Não o utilize contra sistemas de
terceiros sem autorização explícita.
