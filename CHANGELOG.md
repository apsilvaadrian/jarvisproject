# Changelog

Todas as mudanças relevantes do projeto, por versão ([versionamento semântico](https://semver.org/lang/pt-BR/)).

> **Sobre o histórico:** o J.A.R.V.I.S. começou a ser desenvolvido antes do versionamento com Git. O código que já
> existia foi importado em etapas, uma por módulo (v0.1.0 a v0.6.0). Essas tags marcam as etapas da importação, não
> lançamentos feitos naquelas datas. A partir da v0.6.0, cada mudança é commitada normalmente por quem a fez.

## [Não lançado]

- Documentação do projeto em PDF, no modelo ABNT da disciplina: `docs/documentacao.pdf` (Adrian Silva).

## [1.0.0] — 2026-11-22 — Entrega do Projeto Integrador

- Scripts `.sh` com permissão de execução no Git (no Linux, `./scripts/setup.sh` dava "permissão negada") (Adrian Silva).
- Verificação manual no Windows registrada em `docs/verificacao.md` (Gustavo Marini).
- Testes de erro da análise de pasta em `tests/test_analise_pasta.py` (Gustavo Marini).
- Scripts `.sh` validados no Ubuntu 24.04; `monitor.sh` corrigido para Linux em português (memória lida do
  `/proc/meminfo`) e com validação dos limites passados por parâmetro (Igor Correa).
- `scripts/backup.sh`: backup de `data/` e `logs/` por data, com rotação dos antigos, trava com `flock` e pronto
  para o `cron` (Igor Correa).
- `docs/conceitos-so.md`: cada conceito de SO com o trecho do código e o comando Linux equivalente (Igor Correa).
- Testes dos scripts `run.sh` e `monitor.sh` em `tests/test_scripts_shell.py` (Gustavo Marini).
- Testes do `backup.sh` em `tests/test_backup.py` (Gustavo Marini).
- Diagramas da arquitetura (camadas, processos e fluxo de uma ação sensível) em `docs/arquitetura.md` e
  seção "Arquitetura" do README atualizada (Gustavo Marini).
- Prints da verificação manual (terminal e interface) em `docs/img/` (Gustavo Marini).

## [0.6.0] — Janelinha flutuante e apps internos

- Janelinha flutuante com o JARVIS minimizado (peças arrastáveis, modo discreto, vários monitores).
- WhatsApp, Instagram, Facebook, YouTube e Navegador dentro do JARVIS, com tema do JARVIS.
- Navegador interno (modo leitura e modo página), apelidos de contatos e avisos silenciados.

## [0.5.0] — Integrações

- Spotify (com letra sincronizada), WhatsApp (extensão e WhatsApp Web), agenda, GitHub, Gmail, notícias e clima.

## [0.4.0] — Aplicativo de desktop

- `JARVIS.exe` (PyInstaller), ícone na bandeja, janela em tela cheia e boas-vindas ao desbloquear a sessão.

## [0.3.0] — Interface

- Interface web com núcleo animado, painéis de CPU/RAM/disco/rede e cards controlados por voz ou pela IA.

## [0.2.0] — Assistente

- Servidor HTTP + WebSocket, cérebros plugáveis (Claude, GPT, Gemini e modo offline), ferramentas com confirmação
  de ações sensíveis e voz (navegador, ElevenLabs e voz local).

## [0.1.0] — Monitor do sistema

- Monitoramento de CPU, memória, discos, temperatura, rede e processos; encerramento controlado de processos.
- Scripts de instalação, execução e monitoramento (`.sh` e `.ps1`).
