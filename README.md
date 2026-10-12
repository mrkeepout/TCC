# Ataques EDoS no Lightweight SDN Testbed (LST 2.0)

> Trabalho de Conclusão de Curso — Universidade de Brasília (UnB)
> Implementação e análise de ataques **EDoS** (*Economic Denial of Sustainability*) de baixa taxa em ambientes **SDN / edge**, com estudo de **detecção, mitigação e correção**.

![Status](https://img.shields.io/badge/status-em%20desenvolvimento-yellow)
![Python](https://img.shields.io/badge/python-3.x-blue)
![Ubuntu](https://img.shields.io/badge/Ubuntu-24.04%20LTS-orange)
![Base](https://img.shields.io/badge/testbed-LST%202.0-6f42c1)
![License](https://img.shields.io/badge/license-AGPL--3.0-green)

---

## Sobre o projeto

Ataques de negação de serviço de camada de aplicação e **baixa taxa** (*low-and-slow*) não derrubam o alvo por volume: eles seguram recursos com tráfego que parece legítimo. Em ambientes elásticos e de borda — como os cenários **SDN** e **O-RAN** —, esse comportamento deixa de causar apenas indisponibilidade e passa a causar **dano econômico e de sustentabilidade**, ao forçar consumo de recursos escassos e caros. Esse é o fenômeno **EDoS**.

Este trabalho estende o **[Lightweight SDN Testbed (LST 2.0)](https://sol.sbc.org.br/index.php/sbseg_estendido/article/view/21699)** — testbed emulado baseado em contêineres para redes SDN seguras — para implementar esses ataques de forma controlada e reprodutível, gerar tráfego rotulado via **CICFlowMeter** e avaliar formas de **detecção, mitigação e correção**. O resultado é documentado em formato de artigo (padrão SBC).

Para o cenário ser EDoS de verdade, o testbed ganhou dois ingredientes que o LST 2.0 não tinha nativamente: **elasticidade** (um *autoscaler* que provisiona/remove réplicas sob demanda) e **medição de custo** (contabilização de *réplica-segundos* — a "conta" da nuvem). A arquitetura completa está em [`lst2.0/demonstration/README_EDOS.md`](lst2.0/demonstration/README_EDOS.md).

### Ataques nesta fase

| Ataque | Alvo do abuso | Responsável |
|---|---|---|
| **Slowloris** | Headers HTTP incompletos, mantendo o pool de conexões ocupado | Gabriel |
| **RUDY** (*R-U-Dead-Yet?*) | Corpo do POST enviado byte a byte | Matheus |

No testbed, os dois são dirigidos pelo **slowhttptest** (modos *Slowloris* / *Slow POST* / *Slow Read*) contra o **VIP** do *load balancer*. Há ainda uma implementação **didática e autônoma** dos mesmos ataques em `infraestrutura/ataques/`, derivando de uma classe base comum, `SlowAttack`, para estudo isolado fora do testbed.

---

## Estrutura do repositório

```
.
├── infraestrutura/
│   └── ataques/            # slow_attacks.py — Slowloris + RUDY didáticos (classe base SlowAttack)
├── lst2.0/                 # testbed LST 2.0 + topologia EDoS (fork de alexandrekaihara/lst2.0)
│   ├── demonstration/      # edos.py, edos_gui.py, autoscaler, load balancer, plot_edos.py, README_EDOS.md
│   ├── docker/             # imagens dos contêineres (web, controlador, CICFlowMeter, etc.)
│   ├── src/                # núcleo do testbed + dependencies.sh
│   └── test/
├── LST 2.0.pdf             # referências (artigo do testbed e literatura de low-rate DoS / EDoS)
├── Lima-2024.pdf
├── Chowdhury-2017.pdf
├── Saeed-2025.pdf
├── Low-Rate_DoS_Attacks_Detection_Defense_and_Challenges_A_Survey.pdf
├── LICENSE
└── README.md
```

> O testbed LST 2.0 vive embutido em `lst2.0/` (antes era um submódulo; agora é parte do próprio repositório, para que um único `git clone` traga tudo).

---

## Requisitos

- **Ubuntu Desktop 24.04 LTS**
- **Python 3.x**, **Docker** e **Docker Compose**
- **Open vSwitch** (emulação dos switches SDN)
- **CICFlowMeter** — imagem fixada em `lst2.0/docker/` para reprodutibilidade

## Instalação

```bash
# 1. Clonar este repositório (o testbed LST 2.0 já vem embutido em lst2.0/)
git clone https://github.com/mrkeepout/tcc.git
cd tcc

# 2. Instalar as dependências do testbed (Docker, Docker Compose, Pip3, Open vSwitch)
cd lst2.0/src && sudo chmod +x dependencies.sh && sudo ./dependencies.sh
```

---

## Uso

O experimento EDoS completo (topologia + autoscaler + ataque + medição de custo) é orquestrado a partir do testbed:

```bash
cd lst2.0/demonstration
# Configuração e passos de execução em README_EDOS.md.
# edos.py sobe a topologia e conduz o experimento; edos_gui.py oferece uma GUI web
# para configurar e disparar as execuções.
```

Para rodar os ataques slow-rate de forma **didática e isolada**, fora do testbed:

```bash
python3 infraestrutura/ataques/slow_attacks.py slowloris --host <alvo-no-laboratorio>
# ou: rudy
```

Ao final de cada execução no testbed, os fluxos são exportados como CSV (CICFlowMeter), já rotulados por meio dos *logs* de início/fim de cada ataque, e o `plot_edos.py` gera a linha do tempo de réplicas / conexões / custo.

---

## Metodologia (visão geral)

```
Ataque no LST 2.0  →  captura de pacotes  →  CICFlowMeter  →  CSV de features
      →  dataset rotulado  →  detector  →  detecção / mitigação / correção
```

A assinatura de fluxo do *low-and-slow* (duração alta, `Bytes/s` e `Packets/s` mínimos) é justamente o oposto de um DoS volumétrico — e é o que fundamenta a distinção pelo detector. No eixo econômico, o sinal é o *auto-scaling* disparado pelo ataque: réplicas provisionadas e **réplica-segundos** acumulados.

---

## Cronograma

O andamento é acompanhado pelas **[Milestones](../../milestones)** e pelo board em **[Projects](../../projects)**.

| Milestone | Entrega |
|---|---|
| M0 · Setup e fundamentação | 19/ago/2026 |
| M1 · Implementação dos ataques | 26/ago/2026 |
| M2 · Dados + detecção | 02/set/2026 |
| M3 · Draft completo do artigo | 30/nov/2026 |
| M4 · Revisão e finalização | 12/dez/2026 |

---

## Aviso ético e legal

Este repositório contém **código de ataque destinado exclusivamente à experimentação acadêmica controlada** dentro do testbed LST 2.0. O objetivo é o estudo de detecção e defesa. **Não** utilize estas ferramentas contra sistemas, redes ou serviços de terceiros sem autorização — fazê-lo é ilegal e contraria o propósito deste trabalho.

---

## Autores

- **Gabriel** — [@mrkeepout](https://github.com/mrkeepout) — trilha Slowloris
- **Matheus** — [@matw4b](https://github.com/matw4b) — trilha RUDY

**Orientação:** [Marcos Fagundes Caetano] — Departamento de Ciência da Computação, UnB

---

## Licença

Distribuído sob a licença **AGPL-3.0**, em conformidade com o LST 2.0, que este projeto estende. Veja [`LICENSE`](LICENSE).

## Agradecimentos

- Equipe do **LST 2.0 / [UnB-COMNET](https://github.com/UnB-COMNET)** e de [alexandrekaihara/lst2.0](https://github.com/alexandrekaihara/lst2.0)
- **CIC — Canadian Institute for Cybersecurity**, pelo CICFlowMeter
