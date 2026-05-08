# Proposta Refinada de TCC
## Título provisório
Arquitetura segura para orquestração de agentes autônomos baseados em LLM: um estudo de caso na automação de aluguéis de temporada
## 1. Tema e Contextualização
A popularização de modelos de linguagem de grande porte (LLMs) e de agentes autônomos baseados em IA generativa está impulsionando a automação de fluxos de trabalho em diversos setores, especialmente em serviços intensivos em interação com clientes, como o mercado de locações de temporada de curta duração. Ao mesmo tempo, a exposição de APIs, o tratamento de dados pessoais em texto claro e a integração com sistemas legados ampliam a superfície de ataque e os riscos de violação de privacidade sob a ótica da LGPD.[^1][^2][^3]

Nesse cenário, ataques de prompt injection passaram a ser reconhecidos como uma das principais ameaças à segurança de aplicações baseadas em LLM, pois permitem manipular o comportamento do modelo e ocasionar vazamento de dados confidenciais ou execução de ações indevidas. Paralelamente, arquiteturas de rede orientadas a Zero Trust vêm sendo adotadas como paradigma de segurança que assume que nenhum tráfego é confiável por padrão, exigindo autenticação e autorização contínuas, uso de VPNs e segmentação rígida da rede.[^4][^5][^6][^7][^8][^9]

Diante disso, torna-se relevante investigar como integrar princípios de LLM security (LLM Sec), Zero Trust e técnicas de Data Loss Prevention (DLP) na construção de uma arquitetura de orquestração de agentes autônomos voltada a um cenário concreto, como o de gestão operacional de aluguéis de temporada.
## 2. Problema de pesquisa
A adoção de LLMs e agentes autônomos para automatizar fluxos de atendimento, comunicação com hóspedes e operações internas promete ganhos significativos de eficiência e redução de esforço humano. Entretanto, a tramitação de dados pessoais (nomes, documentos, contatos, placas de veículos, dados de reserva etc.) em prompts e respostas pode levar a violações da LGPD, sobretudo em razão de riscos de vazamento, uso indevido e reidentificação de titulares.[^1][^10][^2][^3]

Ao mesmo tempo, ataques de prompt injection — diretos ou indiretos — podem induzir o agente a ignorar políticas de segurança, revelar informações de outros hóspedes, ou disparar ações administrativas não autorizadas por meio de integrações de API. Esses riscos se agravam quando a orquestração de fluxos envolve múltiplos sistemas, acesso a documentos e serviços externos, e quando o ambiente de rede não segue princípios de Zero Trust.[^4][^5][^7][^8][^9]

Dessa forma, o problema científico pode ser formulado da seguinte maneira:

> **Como projetar e implementar uma arquitetura de orquestração de agentes autônomos baseados em LLM que suporte regras de negócio complexas para automação de aluguéis de temporada, garantindo isolamento de rede, conformidade com a LGPD na proteção de dados pessoais e resiliência contra ataques de prompt injection?**

Essa formulação deixa claro o recorte (aluguel de temporada), o artefato central (arquitetura de orquestração com agentes LLM) e os requisitos não funcionais críticos (segurança, privacidade e resiliência a ataques).
## 3. Objetivo geral
Desenvolver e avaliar uma arquitetura de automação segura, baseada em agentes de IA generativa, para a orquestração de atendimento e gestão operacional em aluguéis de temporada, garantindo resiliência contra ataques direcionados ao modelo (LLM Sec) e proteção de dados pessoais sensíveis em conformidade com a LGPD.
## 4. Objetivos específicos
Os objetivos específicos podem ser organizados em dois eixos complementares (Aluno 1 – orquestração e agente; Aluno 2 – segurança, infraestrutura e privacidade):
### 4.1. Foco 1 – Engenharia do Agente e Orquestração (Aluno 1)
- Implementar um orquestrador de fluxos de trabalho (por exemplo, n8n ou equivalente) em ambiente conteinerizado (Docker), responsável por gerenciar o estado da aplicação e a coordenação das chamadas ao LLM e às APIs externas.
- Projetar e implementar o raciocínio do agente, utilizando técnicas de prompt engineering avançado, function calling e ferramentas (tools), de forma que ele seja capaz de interpretar intenções dos usuários, consultar dados estruturados, gerar documentos dinâmicos (por exemplo, PDFs com regras de condomínio e instruções de check-in) e interagir com APIs de calendário e precificação (como PriceLabs ou similar) em um fluxo automatizado.
- Definir e coletar métricas de desempenho do agente, tais como latência de resposta, taxa de sucesso em decisões autônomas (por exemplo, classificação correta de intenções, escolha correta de ações) e redução do tempo total de execução do fluxo operacional em comparação com o processo manual.
### 4.2. Foco 2 – Segurança, Infraestrutura e Privacidade (Aluno 2)
- Projetar uma arquitetura de rede com base em princípios de Zero Trust, restringindo o acesso administrativo ao orquestrador e a outros componentes sensíveis exclusivamente via túneis VPN criptografados (por exemplo, WireGuard), com segmentação e regras de roteamento estritas (por exemplo, utilizando MikroTik ou firewalls equivalentes).[^11][^6][^12][^13][^14]
- Projetar e implementar um middleware de Data Loss Prevention (DLP) capaz de inspecionar, em tempo real, o tráfego de prompts e respostas entre a aplicação e o LLM, identificando e mascarando (redaction) dados sensíveis como nomes, documentos, placas de veículos e outras informações que permitam identificar indivíduos, antes do envio ao modelo.[^1][^10][^2][^3]
- Desenvolver e validar guardrails de segurança para detecção e mitigação de ataques de prompt injection e jailbreak, combinando regras estáticas, filtragem de entradas e saídas e, possivelmente, uso de modelos auxiliares para classificação de prompts maliciosos.[^4][^5][^7][^8][^9]
## 5. Metodologia
A pesquisa terá natureza **aplicada**, com abordagem **experimental** e enfoque em **engenharia de software e segurança de sistemas**. O trabalho pode ser estruturado como pesquisa de desenvolvimento de artefato (Design Science Research), em que se projeta, implementa e avalia uma solução técnica para um problema bem definido.
### 5.1. Etapas metodológicas propostas
1. **Revisão bibliográfica e documental**  
   Levantamento de literatura técnica e acadêmica sobre segurança em LLMs, ataques de prompt injection, arquiteturas Zero Trust, LGPD aplicada à IA generativa e técnicas de DLP em aplicações de processamento de linguagem natural.[^4][^1][^5][^6][^10][^7][^2][^8][^9][^3]

2. **Definição de requisitos e modelagem da arquitetura**  
   - Levantamento dos requisitos funcionais (fluxos de negócio do aluguel de temporada, casos de uso de atendimento, geração de documentos, integração com calendários e precificação).  
   - Definição dos requisitos não funcionais de segurança, privacidade, conformidade com LGPD e desempenho (latência máxima aceitável, taxa máxima de falsos positivos no DLP e nos guardrails, etc.).[^1][^10][^2][^3]
   - Modelagem da arquitetura de referência do sistema, com diagrama de componentes (orquestrador, serviços de IA, middleware de DLP, banco de dados, firewalls, VPN, painéis administrativos), fluxos de dados e zonas de rede.

3. **Implementação do orquestrador e do agente**  
   - Configuração do ambiente conteinerizado (Docker) com o orquestrador de fluxos (n8n ou equivalente) e serviços de apoio.  
   - Implementação dos fluxos principais: onboarding de hóspedes, envio de regras de condomínio, geração de vouchers e mensagens automatizadas.  
   - Implementação do agente LLM com prompts de sistema, tools/function calling e lógica para consulta de dados e geração de documentos.

4. **Implementação da camada de segurança e privacidade**  
   - Configuração da arquitetura de rede com segmentação e acesso administrativo via VPN (WireGuard) e aplicação de princípios Zero Trust.[^11][^6][^12][^13][^14]
   - Desenvolvimento do middleware de DLP para identificação e redaction de dados sensíveis no tráfego entre a aplicação e o LLM.  
   - Implementação de guardrails para filtragem de prompts e respostas, incluindo regras de bloqueio e mitigação de ataques de prompt injection (diretos e indiretos) e tentativas de exfiltração de dados.[^5][^7][^8][^9][^4]

5. **Estudo de caso e experimentos**  
   - Definição de um cenário de gestão de propriedades de curta duração em praças turísticas de alto fluxo (por exemplo, cidades como Natal e Caldas Novas), com perfis de hóspedes, regras de condomínio e políticas internas fictícias, porém realistas.[^2]
   - Execução dos fluxos automatizados no ambiente de teste, com simulação de interações de hóspedes e operadores.

6. **Bateria de testes de segurança (simulação de ameaças)**  
   - Planejamento de casos de teste específicos para segurança em LLMs: tentativas de exfiltração de dados de outros hóspedes, instruções maliciosas camufladas em mensagens de usuários ou documentos, ataques de prompt injection diretos e indiretos.[^7][^8][^9][^4][^5]
   - Execução dos testes de penetração focados na camada de IA (LLM) e na camada de rede, avaliando a eficácia do middleware de DLP e dos guardrails.

7. **Coleta e análise de métricas**  
   - Métricas de infraestrutura: latência adicionada pela camada de DLP, utilização de recursos, impacto na escalabilidade.  
   - Métricas de segurança: taxa de detecção de ataques de prompt injection, taxa de falsos positivos e falsos negativos nos bloqueios de segurança.  
   - Métricas de qualidade do modelo: incidência de alucinações em respostas críticas, precisão na interpretação de intenções e na execução de ações.  
   - Comparação do desempenho do processo automatizado com um baseline manual ou semi-automatizado (quando viável).

8. **Discussão dos resultados e elaboração da monografia**  
   - Análise crítica dos resultados obtidos, identificando trade-offs entre segurança, privacidade, desempenho e usabilidade.  
   - Sistematização de padrões de arquitetura, boas práticas e limitações encontradas.  
   - Redação da monografia, com descrição detalhada da arquitetura proposta, do estudo de caso, da metodologia de testes e dos resultados.
## 6. Estudo de caso: automação em aluguéis de temporada
O estudo de caso será centrado em um cenário de gestão de imóveis de curta duração em destinos turísticos com alta rotatividade de hóspedes, como cidades brasileiras com forte fluxo de turismo de lazer. Nesse contexto, a operação típica envolve: recebimento de reservas, comunicação pré-check-in e pós-check-out, envio de regras de condomínio e instruções de acesso, registro de dados de veículos e hóspedes, bem como atendimento de dúvidas recorrentes.[^2]

Esse domínio é particularmente sensível a questões de privacidade, pois lida com dados pessoais e potencialmente dados sensíveis (por exemplo, preferências, informações de saúde ou necessidades especiais relatadas pelo hóspede). Além disso, a pressão por respostas rápidas e automação completa torna o uso de agentes de IA atrativo, mas aumenta o risco de exposição indevida se os mecanismos de proteção não forem adequados.[^1][^3][^2]

A arquitetura proposta deverá permitir que o agente opere de forma "cega" para dados sensíveis, isto é, recebendo entradas já pseudonimizadas ou mascaradas pelo middleware de DLP, enquanto apenas componentes autorizados mantêm a capacidade de reidentificação quando estritamente necessário e em conformidade com os princípios da LGPD (finalidade, necessidade, minimização, segurança, prestação de contas).[^10][^3][^1][^2]
## 7. Resultados esperados
Como resultados concretos, espera-se:

- Uma **prova de conceito (PoC)** funcional demonstrando, em tempo quase real, um agente de IA operando com autonomia em fluxos de atendimento e gestão operacional de aluguéis de temporada, com isolamento de dados sensíveis por meio do middleware de DLP.
- Um **repositório de código** contendo scripts de orquestração, configuração de infraestrutura (por exemplo, arquivos Docker, configurações básicas de VPN/roteamento) e implementação dos componentes de DLP e guardrails de segurança.
- Uma **monografia** documentando:  
  - a arquitetura proposta (diagramas, componentes, fluxos de dados);  
  - os requisitos de segurança e privacidade considerados, com referência às obrigações da LGPD no uso de IA generativa;[^1][^10][^2][^3]
  - o desenho e os resultados da bateria de testes de segurança focados em ataques de prompt injection;[^4][^5][^7][^8][^9]
  - as métricas de desempenho e os trade-offs observados entre segurança, privacidade e eficiência operacional.
- A sistematização de **padrões de arquitetura e boas práticas** (design patterns) que possam ser replicados ou adaptados para outros contextos corporativos de alta restrição, servindo como referência para equipes que desejem adotar agentes LLM com foco em segurança.
## 8. Viabilidade e contribuições
A proposta é viável para um TCC em dupla em Ciência/Engenharia de Computação, pois permite uma divisão clara de responsabilidades entre engenharia de orquestração/IA e segurança/infraestrutura, mantendo forte integração entre as partes.

Do ponto de vista científico e tecnológico, o trabalho contribui ao:

- Integrar, em um caso de uso concreto, conceitos de LLM security, Zero Trust, DLP e LGPD, um tema emergente na literatura e na prática profissional.[^4][^1][^5][^6][^10][^7][^2][^8][^9][^3]
- Propor e avaliar um arranjo arquitetural reproduzível que pode servir como referência para outras organizações que desejam adotar agentes autônomos com requisitos rígidos de segurança.
- Gerar evidências empíricas (métricas e resultados de testes) sobre o impacto de mecanismos de proteção (DLP, guardrails, Zero Trust) no desempenho e na eficácia de agentes LLM operando em cenários reais de negócio.

---

## References

1. [Uso da IA Generativa e LGPD - NeuralMind](https://neuralmind.ai/blog/uso-da-ia-generativa-e-lgpd) - A ligação entre a LGPD e a IA generativa está no uso de dados pessoais, tanto no processo de treinam...

2. [LGPD e Inteligência Artificial: o que programadores precisam saber](https://hub.asimov.academy/blog/lgpd-ia/) - Entenda como a LGPD se aplica ao uso de inteligência artificial e ao tratamento de dados pessoais em...

3. [Radar_Tecnologico_IA_Generati...](https://www.gov.br/anpd/pt-br/centrais-de-conteudo/documentos-tecnicos-orientativos/radar_tecnologico_ia_generativa_anpd.pdf) - Denúncia de descumprimento da LGPD ... Composição · Composição · Conselho Nacional de Proteção de Da...

4. [Prompt Injection: uma ameaça silenciosa à segurança em IA](https://www.welivesecurity.com/pt/ameacas-digitais/prompt-injection-uma-ameaca-silenciosa-a-seguranca-em-ia/) - A prompt injection compromete a segurança de um dos tipos de IA mais utilizados, os LLM (Large Langu...

5. [O que é um Prompt Injection Attack?](https://www.wiz.io/pt-br/academy/ai-security/prompt-injection-attack) - Os ataques de injeção de prompt são uma ameaça à segurança da IA em que um invasor manipula o prompt...

6. [Arquitetura de Rede Zero Trust – Um Guia Prático para Empresas](https://blog.contractize.app/pt/zero-trust-network-architecture-a-practical-guide-for-enterp/) - Aprenda a projetar, implementar e monitorar uma Arquitetura de Rede Zero Trust que protege empresas ...

7. [O que é um ataque de injeção de prompt? - IBM](https://www.ibm.com/br-pt/think/topics/prompt-injection) - Em ataques de injeção de prompt, os hackers manipulam sistemas generativos de IA, alimentando-os com...

8. [Ataques de Prompt Injection: entenda a ameaça a modelos LLMs](https://vantico.com.br/ataques-de-prompt-injection-ameaca-a-modelos-llms/) - Descubra o que são ataques de Prompt Injection, como afetam sistemas com IA e como preveni-los atrav...

9. [Guia Completo para Prevenir Prompt Injection em LLMs - RDD10+](https://www.robertodiasduarte.com.br/guia-completo-para-prevenir-prompt-injection-em-llms/) - Entenda o que é prompt injection, seus riscos e conheça técnicas eficazes para proteger suas aplicaç...

10. [Proteção de dados pessoais em sistemas de inteligência artificial ...](https://www.bdtd.uerj.br:8443/handle/1/25041) - Buscou-se demonstrar que o direito à eliminação de dados pessoais de sistemas generativos de intelig...

11. [Como Configurar VPN WireGuard Cliente no MikroTik | Blog](https://mkcontroller.com/blog/pt/tutorials/mikrotik/wireguard_vpn_mikrotik/) - Aprenda a configurar WireGuard no MikroTik: guia passo a passo para VPN segura com roteamento e kill...

12. [Configurando o WireGuard em um roteador Mikrotik rodando OpenWrt](https://prohoster.info/pt/blog/nastraivaem-wireguard-na-routere-mikrotik-pod-upravleniem-openwrt) - Mas por enquanto, infelizmente, para configurar o WireGuard em um roteador Mikrotik, você precisa al...

13. [Configuração passo a passo do WireGuard VPN no MikroTik ...](https://www.reddit.com/r/mikrotik/comments/1shvz9s/stepbystep_wireguard_vpn_setup_on_mikrotik/) - No vídeo, eu abordo: Criando a interface WireGuard no MikroTik. Gerando e usando chaves públicas/pri...

14. [Wireguard no MikroTik: Passo a Passo de Configuração ... - YouTube](https://www.youtube.com/watch?v=rkwrtxvFgkk) - Club de Redes - Tenha Conteúdo sobre Redes de Vários Fabricantes com conteúdo novo todo mês em um só...

