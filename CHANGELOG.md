# Changelog

Todas as mudanças relevantes do projeto, por versão ([versionamento semântico](https://semver.org/lang/pt-BR/)).

> **Sobre o histórico:** o J.A.R.V.I.S. começou a ser desenvolvido antes do versionamento com Git. O código que já
> existia foi importado em etapas, uma por módulo (v0.1.0 a v0.6.0). Essas tags marcam as etapas da importação, não
> lançamentos feitos naquelas datas. A partir da v0.6.0, cada mudança é commitada normalmente por quem a fez.

## [Não lançado]

- Validação dos scripts no Linux, script de backup e documentação dos conceitos de SO (Igor Correa).
- Roteiro de verificação executado, novos testes, prints e diagrama da arquitetura (Gustavo Marini).
- Verificação manual no Windows registrada em `docs/verificacao.md` (Gustavo Marini).

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
