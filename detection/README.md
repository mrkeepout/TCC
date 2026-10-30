# Detector de EDoS — anomalia treinada só no benigno

Detector de ataques EDoS (*Economic Denial of Sustainability*) sobre os fluxos do
testbed LST 2.0. A filosofia (sugerida pelo Jefferson): **treinar só com tráfego
benigno** — o modelo aprende "como é o normal" — e, no tráfego com atacantes,
flagrar o que **foge do normal**. Nunca mostramos ataque ao modelo no treino.

## Como rodar

```bash
cd detection
pip install numpy pandas scipy scikit-learn matplotlib   # se faltar algo
python3 edos_detect.py
```

Ele espera os CSVs em `detection/data/` (não versionados — são pesados):

| Arquivo | O que é |
|---|---|
| `run_benign_0atk.csv` | rodada 100% benigna → **treino** |
| `run_1atk.csv` | 9 benignos + 1 atacante → teste |
| `run_2atk.csv` | 9 benignos + 2 atacantes → teste |
| `cost_benign.csv`, `cost_1atk.csv`, `cost_2atk.csv` | série de custo do autoscaler |

Saída: métricas no terminal + `out/results.json` + as figuras em `out/`.

## Como funciona

O detector tem **dois níveis**, porque a investigação mostrou que operam em
unidades diferentes.

### Nível 1 — por-fluxo (distância de Mahalanobis)

Para cada fluxo, a pergunta é "quão longe isso está do normal?". A distância de
Mahalanobis responde isso levando em conta a escala **e a correlação** das
features do tráfego benigno:

```latex
D^2 = (x - \mu)^\top \, \Sigma^{-1} \, (x - \mu)
```

- `μ`, `Σ` = média e covariância das features, estimadas **só no benigno**
  (`build_flow_model`). Σ usa Ledoit-Wolf para ficar bem-condicionada.
- `score_flows` calcula o `D²` de cada fluxo; o corte é o **χ² a 99%** (sob
  normalidade, `D² ~ χ²` com k graus de liberdade = nº de features).
- **Escopo:** só as requisições que chegam ao serviço (`Dst = VIP:80`). Sem
  isso, o baseline mistura cliente (rápido) e servidor (lento) e o ataque afoga.
- **Features (7):** duração, pacotes fwd/bwd, bytes/s, packets/s, IAT média/desvio,
  tamanho médio do pacote fwd. As de cauda longa levam `log1p` antes.

### Nível 2 — agregado por janela

O perigo do low-and-slow é o **agregado**: muitas conexões lentas ao mesmo
tempo. `windowed()` conta, por janela de 10s, as requisições "lentas"
(`Bytes/s < 1000`) e aplica um limiar simples: **média + 3σ do benigno**.

### Limpeza obrigatória

`load()` descarta fluxos com **duração negativa** (~27% — bug conhecido do
CICFlowMeter com pacotes curtos capturados fora de ordem; gera bytes/s negativo
que destruiria μ/Σ). Os descartados são quase todos fluxos curtíssimos, não a
assinatura do ataque.

## O que esperar do comportamento

Com as rodadas atuais (Yo-Yo, pulso 20s on / 20s off):

| | 1 atacante | 2 atacantes |
|---|---|---|
| **Nível 1** — precisão | ~98% | ~98% |
| **Nível 1** — recall | ~57% | ~59% |
| **Nível 1** — AUC | ~0.74 | ~0.75 |
| **Nível 2** — req. lentas/10s (mediana) | 156 | 289 |
| **Nível 2** — janelas flagradas | ~70% | ~70% |
| **Nível 2** — falso-positivo no benigno | 0% | 0% |

Leitura:

- **Nível 1 acerta quase sem errar (precisão 98%), mas deixa passar ~40%.**
  Esperado: fluxo a fluxo, uma conexão slow isolada se parece com uma requisição
  HTTP pequena normal. A AUC ~0.74 reflete isso.
- **Nível 2 separa limpo** (benigno nunca passa de ~29 req. lentas/10s; ataque
  vai a 156–815). Os ~70% — e não 100% — são porque o ataque é **pulsado**: os
  "vales" do Yo-Yo têm pouca atividade. Veja `out/nivel2_janelas.png`: a curva
  oscila. Nos picos, a separação é total.
- **Custo:** o ataque custa ~2.5× o benigno. 1 e 2 atacantes custam quase igual
  **porque o autoscaler satura no teto de 3 réplicas** — artefato do teto, não
  propriedade do ataque.

## Limitações conhecidas (e próximos passos)

- O limiar do Nível 2 (`μ+3σ`, janela de 10s, `Bytes/s < 1000`) é **fixo** — frágil
  a mudanças de pulso/modo/tipo de ataque. Próximo passo: **limiar adaptativo**
  (entropia + EWMA/change-point).
- Treino num **único** benigno; o corte χ² assume gaussianidade (aproximada).
- Slow Read pode não aparecer como "requisição lenta" (a lentidão é na resposta);
  exige features bidirecionais.
