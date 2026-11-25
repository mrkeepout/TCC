# Experimento EDoS sobre o LST 2.0

Topologia e ferramental para emular e estudar ataques **EDoS (Economic Denial of
Sustainability)** em ambientes de nuvem elástica, construída sobre o
[Lightweight SDN Testbed (LST) 2.0](../README.md).

> Trabalho de Conclusão de Curso — **Gabriel (mrkeepout)** e **Matheus (matw4b)**.
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

## Arquitetura (em revisão, será alterada)

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
- **HRF (shadow)** — observador HTTP no ingresso do `lb`; registra o que seria
  encaminhado ao Request Analyzer e possíveis bloqueios, sem interferir no tráfego.
  **Desligado por padrão** (`HRF_ENABLED = False`).

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
| [`edos_metrics.py`](edos_metrics.py) | Pós-processamento: métricas derivadas NCO / EAF / W_yoyo + agregados de QoS |
| [`edos_benchmark.py`](edos_benchmark.py) | Benchmark de capacidade de 1 réplica: acha o ponto de saturação e recomenda `SCALE_UP_CONN`/`SCALE_DOWN_CONN` |
| [`bench_load.py`](bench_load.py) | Gerador de carga (varredura de concorrência) usado pelo benchmark |
| [`loadbalancer/lb.py`](loadbalancer/lb.py) | Proxy TCP round-robin com backends recarregados dinamicamente |
| [`edos_attack/edos_attacker.py`](edos_attack/edos_attacker.py) | Wrapper do slowhttptest (modos `sustained`/`yoyo`) |
| [`client_profiles.py`](client_profiles.py) | Framework de clientes benignos configuráveis (perfis `browsing`/`mailing`/`printing` no motor SURGE) |
| [`http_request_filtering.py`](http_request_filtering.py) | Regras HRF em modo observacional e parser incremental de HTTP/1.x |
| [`pydash_runner.py`](pydash_runner.py) | Executa o pyDash local e coleta eventos de buffer, throughput e QoE |
| [`legit_client.py`](legit_client.py) | Gerador benigno ingênuo (single-rate) — **substituído** por `client_profiles.py`, mantido só para referência |

## Requisitos

- **Linux com Docker** e **privilégios de root** (o LST cria *network namespaces*,
  interfaces veth e regras `iptables`).
- Python 3.8+.
- Acesso à Internet na primeira execução (para baixar as imagens Docker e para os
  atacantes instalarem o `slowhttptest` em runtime).

> Não roda sem root. Em WSL2 funciona desde que o Docker esteja ativo.

### HRF (HTTP Request Filtering) em modo observacional

> **Desligado por padrão.** Para ligar, defina `HRF_ENABLED = True` em
> `edos_config.py` (ou marque a opção na GUI).

O HRF segue o fluxo de Rao et al. (2019): o Request Filter consulta as listas
allow/block; usuários permitidos passam pelo Network Analyzer, onde requisições
grandes são escaladas ao Request Analyzer; usuários desconhecidos seguem ao
Request Analyzer; e este avalia a taxa por IP. A descrição operacional do artigo
não especifica limiares portáveis e não define claramente como o IAT participa
da decisão. Assim, a implementação:

- observa os pedidos HTTP/1.x em texto claro na entrada cliente→`lb` e preserva
  o IP de origem antes do proxy TCP;
- grava requisição, tamanho observado/declarado, classe de origem, taxa e IAT
  em `flows/hrf_events.jsonl`;
- registra o resultado hipotético em `decision` (`forward` / `would_block`),
  mas **nunca descarta ou modifica pedidos**;
- trata tamanho acima do limite como encaminhamento para análise, não como
  classificação automática de ataque; IAT é telemetria, não regra de decisão.

Parâmetros iniciais em `edos_config.py` (`HRF_SIZE_THRESHOLD_BYTES=1048576`,
`HRF_REQUESTS_PER_SECOND_THRESHOLD=20`, janela de 1 s) são **valores
experimentais do testbed, não números do artigo**. Devem ser calibrados com
rodadas benignas, inclusive o perfil pyDash, e registrados no manifesto do run.
A allowlist inicial cobre `.51–.99`; a blocklist começa vazia. HTTP chunked é
observado com tamanho não determinado; HTTPS/TLS não é inspecionado.

> **Limitação:** a allowlist padrão (`.51–.99`) coincide com a faixa de IP usada
> como rótulo benigno (ground truth). Portanto, as métricas HRF calculadas nessa
> configuração são um **limite superior otimista**: a allowlist funciona como um
> oráculo. A avaliação do HRF sem esse oráculo ainda está pendente.

### Usuário pyDash

> **Desligado por padrão** (`PYDASH_ENABLED = False` e sem entrada `pydash` no
> `FLEET` padrão). Para ligar, siga os passos abaixo.

O perfil `pydash` simula um usuário assistindo a um vídeo em streaming DASH. Ele
executa um checkout local do [pyDash](https://github.com/mfcaetano/pydash)
(`git clone https://github.com/mfcaetano/pydash.git`;
`PYDASH_SOURCE_DIR`, sem valor padrão; ex.: `/caminho/para/pydash`) dentro de
um container com as dependências Python do player. O container é construído sob demanda a partir de
`docker/pydash_client/Dockerfile` quando o perfil for selecionado.

Prepare um diretório local com manifesto MPD e todos os segmentos referenciados.
O diretório é montado em modo somente leitura em cada réplica web como
`/dash/1sec` (o componente `1sec` é necessário para o pyDash identificar a
duração dos segmentos),
inclusive nas réplicas provisionadas pelo autoscaler; assim, os arquivos ficam
armazenados apenas uma vez no host. O perfil é selecionável na GUI; preencha o
caminho do checkout e o diretório de mídia. Para configurar fora da GUI, os
dois caminhos vêm de variáveis de ambiente (nenhum caminho de máquina fica no
repositório):

```bash
export PYDASH_SOURCE_DIR=/caminho/para/pydash
export PYDASH_MEDIA_DIR=/caminho/para/conteudo-dash
```

e, em `edos_config.py`:

```python
FLEET = [
    {'profile': 'mixed', 'count': 2},
    {'profile': 'pydash', 'count': 1},
]
PYDASH_ENABLED = True
PYDASH_MPD_FILE = 'manifest.mpd'
PYDASH_MEDIA_URL_PATH = 'dash/1sec'
PYDASH_R2A_ALGORITHM = 'R2A_AverageThroughput'   # ABR adaptativo (padrão)
PYDASH_TRAFFIC_SHAPING = False                   # rede simulada do pyDash desligada (padrão)
EXPERIMENT_DURATION = 660                        # > duração do vídeo (~9min55s)
```

**Algoritmo de adaptação e rede simulada.** O `dash_client.json` original do
pyDash usa `R2AFixed` (sempre a qualidade máxima) e o próprio pyDash emula uma
rede: após cada download ele dorme para seguir uma banda que alterna, a cada
5 s, entre o maior, o intermediário e o menor bitrate do MPD. Com essa
combinação o vídeo trava por construção (a banda média fica muito abaixo da
qualidade pedida), e as pausas medidas não diriam nada sobre o ataque. Por isso
o testbed, por padrão, desliga essa emulação (`PYDASH_TRAFFIC_SHAPING = False`,
só a rede do testbed limita o player) e usa o ABR adaptativo
`R2A_AverageThroughput` (`PYDASH_R2A_ALGORITHM`; também aceita `R2AFixed`,
`R2ARandom` ou qualquer classe R2A do checkout). Ambos ficam na GUI, no bloco
do perfil pyDash.

**Duração.** O pyDash só grava seu próprio resumo quando o vídeo termina; o MPD
Big Buck Bunny dura ~9min55s, então use `EXPERIMENT_DURATION` ≥ ~660 s (a GUI
avisa quando a duração é menor). Se o experimento acabar antes, o teardown
envia SIGTERM ao `pydash_runner.py`, que encerra o player e grava um resumo
**parcial** (`completed: false`) com as pausas observadas até ali.

Com `PYDASH_ENABLED = False`, qualquer entrada `pydash` no `FLEET` é ignorada e
nenhum arquivo do pyDash é exigido. Lembre que `sudo` descarta variáveis de
ambiente por padrão: use `sudo -E` ou `sudo PYDASH_SOURCE_DIR=... PYDASH_MEDIA_DIR=... python3 edos.py`.

O pyDash local usa HTTP na porta 80, espera `Representation@bandwidth` e
`SegmentTemplate@media` com `$Bandwidth$`/`$Number$`, e deriva a duração do
segmento de um componente de caminho como `1sec`. Use mídia finita para que o
player conclua o download e grave seu resumo. O orquestrador copia o checkout
sem `.git`/`venv`, preserva os demais parâmetros do `dash_client.json` local,
aponta o player para o VIP e o MPD emulado e define o algoritmo R2A. Apenas a
cópia temporária do player recebe os patches (URL relativa dos segmentos,
eventos de pausa em tempo real e, se configurado, a emulação de rede
desligada); o checkout do usuário não é alterado. Se a versão do pyDash não
tiver exatamente as linhas que os patches esperam, o run falha em vez de rodar
um player sem patch. O serviço valida o MPD pelo VIP antes de começar a
captura.

Após o run, cada player gera `flows/pydash_lN/pydash.log`,
`pydash_events.csv`, `pydash_qoe.json` e os gráficos produzidos pelo pyDash.
O resumo (`pydash_qoe.json`) contém se a reprodução foi completa
(`completed`), número de pausas e tempo total/médio parado, segmentos baixados,
amostras de buffer (em segmentos) e throughput. A GUI mostra esses valores no
card **QoE do streaming (pyDash)** dos resultados.
Esses resultados devem ser analisados junto da QoS HTTP dos demais perfis e do
`flows/hrf_events.jsonl`; as taxas HRF precisam de calibração com a distribuição
de pedidos DASH real.

## Como executar

```bash
cd lst2.0
cd demonstration
sudo python3 edos_gui.py
```

Na primeira vez, a instanciação demora mais porque as imagens são baixadas do Docker
Hub. Para encerrar, pressione **CTRL+C uma vez** — o experimento derruba todos os
containers, gera o relatório de fluxos e imprime o resumo de custo.

Após o run, para gerar o gráfico da linha do tempo e as métricas derivadas:

```bash
python3 plot_edos.py       # flows/edos_plot.png (réplicas, conexões, custo)
python3 edos_metrics.py    # flows/edos_metrics.json (NCO / EAF / W_yoyo + QoS)
```

### Calibrar os limiares do autoscaler

Os limiares `SCALE_UP_CONN`/`SCALE_DOWN_CONN` devem vir da **capacidade medida** de uma
réplica, de acordo com a máquina que o experimento será executado. O benchmark sobe uma topologia mínima (1 réplica + 1 cliente),
lê o teto configurado do Apache (`MaxRequestWorkers`) e varre níveis de concorrência para
achar o **ponto de saturação** (maior concorrência ainda dentro do SLO de sucesso + p95):

É possível rodar o benchmark diretamente da interface gráfica ou via CLI. 
```bash
sudo python3 edos_benchmark.py   # escreve flows/benchmark.json e imprime os limiares sugeridos
```

Ele recomenda `SCALE_UP_CONN = ponto de saturação × SCALE_TARGET_UTIL` (padrão 75%) e
`SCALE_DOWN_CONN = ponto de saturação × SCALE_DOWN_UTIL` (padrão 25%) — copie para o `edos_config.py`.

## Parâmetros principais (`edos_config.py`)

| Parâmetro | Significado |
|---|---|
| `MIN_REPLICAS` / `MAX_REPLICAS` | Limites do pool web |
| `SCALE_UP_CONN` / `SCALE_DOWN_CONN` | Limiares de conexões **por réplica** para escalar — calibre com `edos_benchmark.py` |
| `SCALE_TARGET_UTIL` / `SCALE_DOWN_UTIL` | Frações da capacidade (ponto de saturação) que viram os limiares acima (padrão 75% / 25%) |
| `TICK_SECONDS` | Intervalo de amostragem/decisão do autoscaler |
| `COOLDOWN_SECONDS` | Tempo mínimo entre duas ações de scaling |
| `WARMUP_SECONDS` | Tempo que uma réplica nova fica **cobrada mas fora do pool** (janela que o yo-yo explora) |
| `DRAIN_TIMEOUT_SECONDS` | Janela de *connection draining* no scale-down antes de remover a réplica |
| `PRICING_PROFILES` / `PRICING_CLOUD` / `PRICING` | Perfis de custo por nuvem (**AWS / Azure / GCP**) + nuvem ativa. Na GUI, um dropdown carrega o padrão da nuvem escolhida e ainda permite editar cada valor. Cada número é citado em `sources`. GCP não tem unidade por conexão (LCU=0; custo de LB via dados/banda); Azure cobra VM por segundo sem mínimo de 60s |
| `COST_PER_REPLICA_SEC` | *(legado)(vai ser removido)* constante antiga mantida só para referência; o custo real vem de `PRICING`  |
| `ATTACK_MODE` | `sustained` ou `yoyo` |
| `SLOW_TEST` | `slowloris` / `slowpost` / `slowread` |
| `SLOW_CONNS` / `SLOW_RATE` / `SLOW_INTERVAL` | Força do slowhttptest (`-c` / `-r` / `-i`) |
| `PULSE_ON` / `PULSE_OFF` | Duração dos pulsos no modo `yoyo`. **Precisam respeitar o ciclo de vida da réplica**: `PULSE_ON ≥ WARMUP_SECONDS` e `PULSE_OFF ≥ SCALE_DOWN_TICKS·TICK + DRAIN_TIMEOUT` — senão o serviço nunca escala de volta e o yo-yo degenera em `sustained` |
| `FLEET` | Tráfego benigno declarativo: lista de `{profile, count}` (quantos e de qual tipo) |
| `PYDASH_ENABLED` / `PYDASH_SOURCE_DIR` | Habilita o perfil pyDash (padrão `False`) e aponta para o checkout local (variável de ambiente `PYDASH_SOURCE_DIR`) |
| `PYDASH_MEDIA_DIR` / `PYDASH_MPD_FILE` | Diretório local de mídia DASH (variável de ambiente `PYDASH_MEDIA_DIR`) e MPD relativo a ele |
| `PYDASH_MEDIA_URL_PATH` | Caminho em que os assets DASH são servidos nas réplicas |
| `PYDASH_R2A_ALGORITHM` | Algoritmo ABR do pyDash: `R2A_AverageThroughput` (padrão, adaptativo), `R2AFixed`, `R2ARandom` |
| `PYDASH_TRAFFIC_SHAPING` | Emulação de rede interna do pyDash; padrão `False` (só a rede do testbed limita o player) |
| `HRF_ENABLED` | Ativa o registro HRF passivo no proxy de entrada (sem bloquear tráfego); padrão `False` |
| `HRF_SIZE_THRESHOLD_BYTES` | Tamanho declarado/observado que encaminha pedido à análise HRF |
| `HRF_REQUESTS_PER_SECOND_THRESHOLD` / `HRF_RATE_WINDOW_SECONDS` | Limite experimental por IP e janela móvel, não fornecidos pelo artigo |
| `SEED` | Semente única do run, propagada aos clientes (reprodutibilidade) |
| `NUM_CLIENTS` / `NUM_ATTACKERS` | Nº de atacantes; `NUM_CLIENTS` é fallback se `FLEET` estiver vazia |
| `PCAP_ROTATE_SECONDS` | Intervalo de rotação do pcap (maior ⇒ corta menos conexões slow) |
| `HEALTHCHECK_RETRIES` / `HEALTHCHECK_INTERVAL` | Tentativas/espera do health-check do VIP antes de medir |

Sugestão de configuração padrão: (yo-yo que de fato oscila com warmup/drain de 30s): `MIN=1, MAX=3, TICK=5s,
WARMUP=30s, DRAIN=30s, PULSE_ON=60s, PULSE_OFF=60s, EXPERIMENT_DURATION=360s` (3 ciclos),
`SLOW_CONNS≈200`. Calibre `SCALE_UP_CONN`/`SCALE_DOWN_CONN` com `edos_benchmark.py`
(a capacidade real da réplica, não um chute).

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
  e **rotuladas** na coluna `Label` por faixa de IP (`attack` p/ `.103–.254`,
  `benign` p/ `.51–.99`). O rótulo checa **os dois extremos** do fluxo, então uma
  conexão slow cortada pela rotação do pcap (direção invertida pelo CICFlowMeter)
  ainda é rotulada corretamente. Ver [`edos_labeling.py`](edos_labeling.py).
- **`flows/run_manifest.json`** — manifesto do run (git SHA, timestamp, semente,
  duração, frota expandida e a configuração efetiva), para o dataset ser
  regenerável e permitir o split Train/Test por run.
- **`flows/qos_l*.csv`** — QoS por requisição de cada cliente benigno (latência,
  status HTTP e sucesso/erro), copiado para fora do container **antes** do
  teardown. É a dimensão de disponibilidade: o dano colateral do ataque sobre o
  usuário legítimo.
- **`flows/hrf_events.jsonl`** — observações e decisões candidatas do HRF em
  shadow mode, sem bloqueio; inclui IAT e razões de encaminhamento.
- `edos_metrics.py` inclui precisão, recall e taxa de falso positivo HRF usando
  as faixas IP do testbed como ground truth de avaliação. A allowlist HRF padrão
  (`HRF_ALLOWLIST`, `.51–.99`) coincide com a faixa benigna usada como rótulo,
  então essas métricas, nesse modo, são um **limite superior otimista**; a
  avaliação sem esse oráculo está pendente.
- **`flows/pydash_lN/`** — logs, eventos CSV, resumo de QoE e figuras de cada
  cliente pyDash.
- **`flows/edos_metrics.json`** — métricas derivadas (rode `python3 edos_metrics.py`
  após o experimento): **NCO** (overhead de custo normalizado sobre o baseline),
  **EAF** (fator de amplificação econômica = dano por byte do atacante) e
  **W_yoyo** (fração de réplica-segundos desperdiçada nas fases `PULSE_OFF`), além
  de taxa de sucesso e percentis de latência do QoS (inclusive por fase ON/OFF).
- **`flows/benchmark.json`** — resultado do `edos_benchmark.py`: teto configurado do
  Apache, a varredura de concorrência, o ponto de saturação e os limiares
  `SCALE_UP_CONN`/`SCALE_DOWN_CONN` recomendados.

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
