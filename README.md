# Ataques EDoS no Lightweight Fog Testbed (LFT)

> Trabalho de Conclusão de Curso — Universidade de Brasília (UnB)
> Implementação e análise de ataques **EDoS** (*Economic Denial of Sustainability*) de baixa taxa em ambiente Fog/Edge, com estudo de **detecção, mitigação e correção**.

![Status](https://img.shields.io/badge/status-em%20desenvolvimento-yellow)
![Python](https://img.shields.io/badge/python-3.x-blue)
![Ubuntu](https://img.shields.io/badge/Ubuntu-24.04%20LTS-orange)
![Base](https://img.shields.io/badge/testbed-LFT%20(profissa__lft)-6f42c1)
![License](https://img.shields.io/badge/license-AGPL--3.0-green)

---

## Sobre o projeto

Ataques de negação de serviço de camada de aplicação e **baixa taxa** (*low-and-slow*) não derrubam o alvo por volume: eles seguram recursos com tráfego que parece legítimo. Em ambientes elásticos e de borda — como os cenários **Fog/Edge** e **O-RAN** —, esse comportamento deixa de causar apenas indisponibilidade e passa a causar **dano econômico e de sustentabilidade**, ao forçar consumo de recursos escassos e caros. Esse é o fenômeno **EDoS**.

Este trabalho estende o **[Lightweight Fog Testbed (LFT)](https://github.com/UnB-COMNET/lft)** para implementar esses ataques de forma controlada e reprodutível, gerar tráfego rotulado via **CICFlowMeter** e avaliar formas de **detecção, mitigação e correção**. O resultado é documentado em formato de artigo (padrão SBC).

### Ataques nesta fase

| Ataque | Alvo do abuso | Responsável |
|---|---|---|
| **Slowloris** | Headers HTTP incompletos, mantendo o pool de conexões ocupado | Gabriel |
| **RUDY** (*R-U-Dead-Yet?*) | Corpo do POST enviado byte a byte | Matheus |

Ambos derivam de uma classe base comum, `SlowAttack`, seguindo o padrão de herança do próprio LFT (`Node → Host`).

---

## Estrutura do repositório

```
.
├── attacks/            # SlowAttack (base) + Slowloris + RUDY
├── docker/             # imagens do contêiner-vítima e do atacante
├── experiment/         # definição e execução dos cenários
├── detection/          # extração de features e detector baseline
├── datasets/           # CSVs rotulados gerados pelo CICFlowMeter
├── results/            # figuras e métricas de avaliação
├── paper/              # fonte do artigo (LaTeX / referências)
└── README.md
```

> Estrutura proposta — ajuste conforme o projeto evoluir.

---

## Requisitos

- **Ubuntu Desktop 24.04 LTS** (recomendado pelo LFT)
- **Python 3.x** e **Docker**
- **OpenvSwitch** (emulação dos switches)
- **CICFlowMeter** — versão fixada em `docker/` para reprodutibilidade

## Instalação

```bash
# 1. Base do testbed
pip3 install profissa_lft

# 2. Este repositório
git clone https://github.com/<usuario>/<repo>.git
cd <repo>

# 3. Dependências do LFT, se necessário
git clone https://github.com/alexandrekaihara/lft
cd lft && chmod +x dependencies.sh && ./dependencies.sh
```

## Uso

```bash
# Exemplo: subir a topologia e executar um cenário de ataque
cd experiment
python3 run_slowloris.py     # ou run_rudy.py
```

Ao final da execução, os fluxos são exportados como CSV (CICFlowMeter) em `datasets/`, já rotulados por meio dos *logs* de início/fim de cada ataque.

---

## Metodologia (visão geral)

```
Ataque no LFT  →  captura de pacotes  →  CICFlowMeter  →  CSV de features
      →  dataset rotulado  →  detector  →  detecção / mitigação / correção
```

A assinatura de fluxo do *low-and-slow* (duração alta, `Bytes/s` e `Packets/s` mínimos) é justamente o oposto de um DoS volumétrico — e é o que fundamenta a distinção pelo detector.

---

## Cronograma

O andamento é acompanhado pelas **[Milestones](../../milestones)** e pelo board em **[Projects](../../projects)**.

| Milestone | Entrega |
|---|---|
| M0 · Setup e fundamentação | 31/ago/2026 |
| M1 · Implementação dos ataques | 30/set/2026 |
| M2 · Dados + detecção | 31/out/2026 |
| M3 · Draft completo do artigo | 30/nov/2026 |
| M4 · Revisão e finalização | 12/dez/2026 |

---

## Aviso ético e legal

Este repositório contém **código de ataque destinado exclusivamente a experimentação acadêmica controlada** dentro do testbed LFT. O objetivo é o estudo de detecção e defesa. **Não** utilize estas ferramentas contra sistemas, redes ou serviços de terceiros sem autorização — fazê-lo é ilegal e contraria o propósito deste trabalho.

---

## Autores

- **Gabriel** — [@handle](https://github.com/) — trilha Slowloris
- **Matheus** — [@handle](https://github.com/) — trilha RUDY

**Orientação:** [Orientador(a)] — Departamento de Ciência da Computação, UnB

---

## Licença

Distribuído sob a licença **AGPL-3.0**, em conformidade com o LFT, que este projeto estende. Veja [`LICENSE`](LICENSE).

## Agradecimentos

- Equipe do **[LFT / UnB-COMNET](https://github.com/UnB-COMNET/lft)** e do projeto **PROFISSA**
- **CIC — Canadian Institute for Cybersecurity**, pelo CICFlowMeter
