# Plano de implementação — EDoS testbed (resposta à auditoria)

28/10/2026 · Gabriel Nascimento

## Triagem da auditoria

A auditoria é boa e, no essencial, está certa. Dois itens que ela levanta (#1 e #2) são bugs que **invalidam qualquer dataset gerado hoje** — entram antes de tudo. Um item (#5, SDN) eu recomendo **adiar**: é quase uma segunda contribuição e conflita com "estabilidade e começar os experimentos". O resto alinha com o que foi pedido.

| Item da auditoria | Veredito | Fase |
| --- | --- | --- |
| #1 Captura duplicada no `br0` (LB proxia → cada conexão cruza 2x) | Aceitar — bug que polui o dataset | Fase 0 |
| #2 Rotulagem: coluna `Label` vazia + flip de Src/Dst em fluxo sem SYN | Aceitar — sem isto o dataset não é rotulado | Fase 0 |
| #3 Benigno ingênuo (`legit_client.py`) | Aceitar — já era prioridade | Fase 1 |
| #4 Realismo do autoscaler (boot, franquia, draining) | Aceitar — prioridade nº 2 | Fase 2 |
| Modelo de custo (LCU estilo AWS em vez de constante) | Aceitar, mas parametrizável (ver ressalva) | Fase 2 |
| Benigno Opção A (Selenium `browsing.py`) | Rejeitar como motor | — |
| Benigno Opção B (SURGE analítico em Python) | Aceitar — é o caminho | Fase 1 |
| Benigno Opção C (Locust / replay de logs) | Opcional/futuro | — |
| Métricas derivadas (NCO, EAF, W_yoyo) | Aceitar | Fase 3 |
| #5 Controlador SDN passivo → mitigação OpenFlow | **Adiar** (fora do MVP) | Futuro |

**Minha discordância (item #5).** Transformar o Ryu de switch L2 em mitigador OpenFlow ativo é um projeto à parte — é o Exp 4 da própria auditoria e praticamente um segundo artigo. Fazer isso agora atrasa os experimentos e adiciona superfície de instabilidade justamente no componente mais frágil de mexer (o controlador). Coletar *estatísticas* OpenFlow (`OFPFlowStatsRequest`) é barato e pode entrar como telemetria extra; a *regra de mitigação* fica para trabalho futuro.

**Ressalva sobre o modelo de custo.** Trocar `COST_PER_REPLICA_SEC` arbitrário pela fórmula LCU da AWS é um ganho real, mas cuidado para não apenas substituir "constante arbitrária" por "números arbitrários da AWS". Implemente como um **perfil de preços plugável** (um dict de parâmetros com a fonte de cada número documentada), para ser defensável no artigo e trocável (AWS/GCP/Azure).

## O plano em fases

A ordem segue uma regra: **nada de rodar experimento antes do dataset ser válido e reprodutível.** Por isso a Fase 0 (correções de captura/rotulagem e estabilidade) vem antes do que foi pedido como prioridade — não adianta ter clientes lindos e autoscaler fiel se cada run produz CSV duplicado e sem rótulo.

A **fronteira do MVP** (o mínimo para um experimento *válido*) é o fim da Fase 2. A Fase 3 é instrumentação que dá as métricas finais — pode começar em paralelo, mas não bloqueia o primeiro run.

| Fase | Entrega | Cobre | No MVP? |
| --- | --- | --- | --- |
| 0 | Dataset válido e reprodutível: dedup de captura, rotulagem bidirecional, seed determinística, manifesto de run | #1, #2 | Sim (pré-requisito) |
| 1 | Framework de clientes: frota configurável (quantos + qual tipo), perfis benignos | #3, benigno B | Sim (prioridade 1) |
| 2 | Autoscaler fidedigno + custo realista: boot delay, franquia mínima, draining, custo LCU parametrizável | #4, custo | Sim (prioridade 2) |
| 3 | Instrumentação: QoS por requisição, CSV de custo enriquecido, métricas NCO/EAF/W_yoyo | métricas | Parcial (pode ir em paralelo) |
| Futuro | Mitigação SDN OpenFlow; replay de logs reais; mais vetores; atacante interno | #5, Exp 4 | Não |

As Fases 0–2 são majoritariamente independentes entre si no código (captura/rotulagem, clientes, autoscaler são arquivos diferentes), então dá para a dupla dividir: enquanto um fecha a Fase 0, o outro adianta a Fase 1 ou 2. Só convergem no primeiro run integrado.

## Fase 0 — dataset válido e reprodutível

Sem esta fase, todo run produz dados que não servem para publicar. São mudanças pequenas e de alto retorno.

**0.1 — Dedup de captura (#1).** O `lb` proxia na mesma sub-rede, então cada requisição aparece duas vezes no `br0`: cliente↔LB e LB↔réplica. Hoje `collectFlows(sniffAll=True)` grava as duas. Decisão de projeto: **qual segmento é o dataset.** Recomendo o **ponto de vista de borda (cliente↔LB)** — é onde um detector real seria posto e onde o low-and-slow se manifesta. Implementação: filtrar os fluxos cujo Src/Dst é o VIP do LB vs. os backends `w*`, ou capturar só o tráfego de ingresso. Isso elimina metade das linhas (as com `Src IP = LB`).

**0.2 — Rotulagem bidirecional + join temporal (#2).** Dois problemas distintos:

- A coluna `Label` nunca é preenchida no `convertPcap`. Preencher por faixa de IP: `.71–.79` (atacantes) → `attack`; `.51–.59` (clientes) → `benign`.
- Como o `tshark` rotaciona o PCAP a cada 60s, uma conexão slow longa pode ser cortada e, sem o SYN, o CICFlowMeter infere a direção errada (inverte Src/Dst). Por isso o rótulo tem que checar **os dois extremos** do fluxo contra a faixa de ataque, não só o Src. Mitigação adicional: aumentar o `rotateInterval` ou alinhar com o flow-timeout para cortar menos conexões no meio.
- Cruzar cada fluxo com o `edos_cost.csv` (réplicas/custo por tick) e com os `ATTACK_START`/`ATTACK_END` por janela temporal — assim cada linha carrega também o contexto de carga/custo do instante.

**0.3 — Reprodutibilidade (adição minha, não estava na auditoria).** Um dataset precisa ser regenerável. Hoje o benigno usa `random` sem seed. Introduzir uma **seed determinística por run** (propagada a clientes e ataque) e um ponto único de configuração. Isso também é condição para o split por run do Exp 3.

**0.4 — Manifesto de run (adição minha).** Cada execução grava um `run_manifest.json`: git SHA, timestamp, config efetiva (todos os parâmetros), seed, duração. Sem isso não há rastreabilidade e o "split Train/Test por run independente" (regra de validação do Exp 3) fica impossível de reconstruir depois.

**0.5 — Health-check antes de medir (estabilidade).** Antes de iniciar captura/ataque, confirmar que o VIP responde e a réplica inicial serve (o próprio README já sugere o teste `wget`). Aborta cedo se a topologia não subiu, em vez de gerar um run silenciosamente vazio.

## Fase 1 — framework de clientes configurável

Requisito: **escolher quantos e de qual tipo** cada cliente é. A forma elegante é reusar o padrão que vocês já têm no `slow_attacks.py`: um Template Method onde a base cuida do ciclo de vida e cada *perfil* preenche só o comportamento. Cada perfil benigno vira uma subclasse do mesmo abstrato "perfil de cliente" — muda só o que cada um faz.

**Motor do benigno (Opção B — SURGE analítico).** Em Python puro (`http.client`/`requests` + threads), sem Selenium. Por perfil benigno: sessões HTTP Keep-Alive, 3–6 requisições por sessão, think-time de cauda pesada (lognormal/Pareto, 1–40s como a CIDDS), tamanhos de objeto seguindo Zipf, e uma fração de POST (~20%) para que RUDY não seja separável só por "existe POST?". (Depende do servidor servir objetos variados — Fase 2/pré-requisito do web server.)

**Perfis como configuração.** Em vez de `NUM_CLIENTS = 2`, uma lista declarativa de frota. Esboço:

```python
FLEET = [
    {"profile": "web_browser",  "count": 4},   # SURGE: navegação humana
    {"profile": "api_poller",   "count": 2},   # GETs periódicos curtos (app/mobile)
    {"profile": "bulk_uploader","count": 1},   # POSTs grandes legítimos
]
```

O orquestrador (`createClients`) lê essa lista, aloca IP por faixa conforme a classe e sobe o perfil certo em cada container. Começar com 2–3 perfis benignos já basta para o MVP; a lista cresce depois sem tocar no código.

**Atacante interno — fora do escopo (decisão: só atacantes externos).** O ataque continua vindo dos hosts externos `a1..aN` na faixa `.71–.79`, como hoje. Um perfil de atacante partindo de dentro da LAN dos clientes fica como extensão futura; por ora o framework de perfis cobre só os benignos.

**Abstração (espelha o `SlowAttack`).**

```python
class ClientProfile(ABC):
    @abstractmethod
    def run(self): ...          # o laço de vida do cliente

class WebBrowser(ClientProfile):   # SURGE benigno
class ApiPoller(ClientProfile):    # GETs curtos periódicos
class BulkUploader(ClientProfile): # POSTs grandes legítimos
```

Assim "de qual tipo" vira só escolher a subclasse, e novos comportamentos entram sem mexer no orquestrador.

## Fase 2 — autoscaler fidedigno + custo realista

Hoje o autoscaler é instantâneo e cobra linear por tick. Isso **não é um detalhe de polimento — é load-bearing para a contribuição do yo-yo.** Sem atraso de boot e sem franquia mínima de cobrança, atacar em pulsos (yoyo) custa quase o mesmo que atacar continuamente (sustained), e o resultado econômico que diferencia os dois regimes desaparece. Realismo aqui é o que faz o número do artigo existir.

**2.1 — Atraso de boot/warmup.** Réplica provisionada não serve na hora: entra em estado `warming` por um tempo configurável (30–60s, como VM/instância real) antes de ir para o pool do LB. Durante o warmup ela **já conta para o custo** (você paga pelo boot) mas **não atende** — que é exatamente a janela que o yo-yo explora.

**2.2 — Franquia mínima de cobrança (billing quantum).** Toda réplica é cobrada por no mínimo N segundos (ex. 60s) mesmo que seja desligada antes. É o que pune o scale-up/down rápido do yo-yo. Trocar a contabilidade atual (`replica_seconds += replicas * TICK`) por uma que registra, por réplica, `início → fim` e cobra `max(vida_real, quantum)`.

**2.3 — Connection draining no scale-down.** Hoje `node.delete()` faz `docker rm -f` imediato — derruba clientes legítimos ativos naquela réplica (prejudica o QoS e falsifica a disponibilidade). Correto: tirar do pool do LB, esperar as conexões ativas drenarem (timeout configurável) e só então remover.

**2.4 — Modelo de custo LCU parametrizável.** Substituir a constante única por um custo com três componentes (estilo AWS EC2/Fargate + ALB), num perfil de preços documentável:

```latex
C_{total}(T) = \sum_k p_{VM}\, R_{billed}(t_k)\,\Delta t \;+\; \sum_k p_{LCU}\, \max\!\left(\frac{C_{conn}(t_k)}{3000},\, \frac{R_{new}(t_k)}{25}\right)\Delta t \;+\; p_{BW}\sum_k B(t_k)
```

Onde `R_billed` usa a franquia mínima (2.2), `C_conn` são conexões concorrentes e `R_new` novas conexões/s (as duas dimensões do LCU da ALB). Cada constante (`p_VM`, `p_LCU`, `3000`, `25`, `p_BW`) vai num dict com a fonte citada — defensável e trocável por nuvem.

**2.5 — Estabilidade do laço.** Vocês já resolveram dois pontos críticos (debounce `SCALE_DOWN_TICKS` e o contador `lb_conns` em vez do `ss` que colapsa sob saturação). Manter, e garantir que o estado novo (warming, draining, vidas de réplica) não reintroduza flapping — um pequeno teste do laço do autoscaler com sinal sintético ajuda a travar isso antes de rodar com a topologia inteira.

## Fase 3 — instrumentação e métricas

Pode começar em paralelo às Fases 1–2; só precisa estar pronta antes de *medir* um experimento, não antes do primeiro run.

- **QoS por requisição (benigno).** Cada cliente benigno registra latência e sucesso/erro por requisição em CSV, **antes do teardown** (hoje os logs morrem dentro do container destruído). É o que mede o dano colateral do ataque sobre o usuário legítimo — a dimensão de disponibilidade/QoS ao lado da econômica.
- **CSV de custo enriquecido.** Estender o `edos_cost.csv` com as colunas do modelo LCU: réplicas cobradas (com franquia), conexões concorrentes, novas conexões/s, custo por componente (VM/LCU/BW).
- **Métricas derivadas.** Calcular no pós-processamento: NCO (overhead de custo normalizado), EAF (fator de amplificação econômica = dano por byte enviado pelo atacante) e W_yoyo (fração de réplica-segundos desperdiçada durante os `PULSE_OFF`). São os números que sustentam o argumento "low-and-slow causa mais dano financeiro por byte".

Tudo isso é leitura dos CSVs que as Fases 0–2 já produzem, então é código de análise, não de infra — baixo risco.

## Fora do MVP + decisões

**Adiado (não fazer agora):** mitigação SDN OpenFlow ativa (#5 / Exp 4) — segunda contribuição, mexe no componente mais frágil; replay de logs reais (benigno Opção C) — bom para o futuro, não essencial; atacante interno; matriz completa de vetores (varrer todos os N, c, i de uma vez) — vem depois do primeiro run válido.

**Definition of done do MVP (um experimento válido).** Um run `sustained` e um `yoyo`, com frota mista (2–3 perfis benignos + atacantes externos), que produz: dataset rotulado e sem duplicatas, `edos_cost.csv` com custo realista, QoS do benigno, e manifesto de run. Se esses dois runs saírem limpos e reproduzíveis, vocês têm o Exp 1 em versão mínima e podem escalar a matriz.

**Decisões (status):**

1. **Ponto de vista da captura (0.1) — RESOLVIDO: borda (cliente↔LB).** Segmento LB↔backend rotulado à parte fica como extensão futura (cross-layer do Exp 3).
2. **Rotulagem — RESOLVIDO: só atacantes externos.** Atacantes ficam na faixa `.71–.79`, fora da LAN dos clientes → rótulo por faixa de IP, simples. O manifesto continua no plano para reprodutibilidade, mas não é mais necessário para o rótulo.
3. **Nuvem de referência do custo — RESOLVIDO: modelo AWS (EC2/Fargate + ALB LCU)**, implementado como perfil de preços plugável.
4. **Perfis benignos iniciais — nota do Gabriel:** "Por enquanto vamos seguir com os comportamentos do LST mesmo, ORAN fica um pouco de fora no momento — para trabalhos futuros." *(Em aberto: confirmar se isso significa usar os perfis do próprio LST — `browsing`, `mailing`, `printing` etc. — ou manter `web_browser` + `api_poller` + `bulk_uploader` como lista inicial.)*
