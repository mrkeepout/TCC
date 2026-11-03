# Calibração do detector por janela (02/11/2026)

Scripts de análise que sustentam os resultados R1–R5 das anotações de 02/11.
Rodam de qualquer pasta (`python3 detection/calibracao/<script>.py`) e leem
`detection/data/` (00–04 = bateria 1 do Matheus; `calibracao/` = 5 rodadas,
arquivo 1..5 = 100, 25, 50, 75, 150 conexões).

| Script | O que faz | Resultado |
|---|---|---|
| `profile.py` | raio-X das 50 rodadas: duração dos fluxos, rótulos, réplicas, custo | achado da captura cortada |
| `calib.py` | **detector por janela** (`j` = 5–60s, 1D e Mahalanobis 5 features), treino benignas 1–5, limiar p99 benignas 6–10; detecção do 1º pulso da bateria 1 + afinamento | R3 (bateria 1); saída `out/calibracao/calib_rows.csv`, `calib_thr_fpr.json` (~3 min) |
| `check0.py` | remove 100% do atacante e roda o detector | R5 (rastro nos legítimos) |
| `check1.py` | alarmes por segmento nas benignas; em que janelas dispara sem atacante | R5 + FPR por trecho (fim da rodada) |
| `calib_real.py` | detector nas 5 rodadas de calibração, por pulso e `j` | R3 (calibração); `out/calibracao/calib_real_rows.csv` |
| `check_off.py` | % de alarme durante o pulso vs no período ocioso | prova de que não é ruído/diferença de versão |
| `lat.py` | latência do alarme vs início do scale-up | R4 |

`check0/check1/check_off/lat/calib_real` reaproveitam as funções de `calib.py`.
Dependências: `pandas numpy scikit-learn`.
