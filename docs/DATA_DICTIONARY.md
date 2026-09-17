# Dicionário das bases

## `processed/search_hits`
Tabela metodológica de descoberta. Uma mesma unidade `video_id` pode aparecer várias vezes se foi encontrada por queries, temas, ordens ou janelas diferentes.

Chaves/variáveis principais: `search_id`, `theme`, `query`, `search_order`, `search_published_after`, `search_published_before`, `page_number`, `result_position`, `video_id`, `channel_id`, `captured_at`.

## `processed/channels`
Uma linha por `channel_id`. Contém metadados do canal, inscritos, visualizações, número de vídeos, playlist de uploads e tópicos quando disponíveis.

## `processed/videos`
Uma linha por `video_id`. Contém metadados, estatísticas, duração, idioma, tópicos e, quando coletada, a transcrição em `transcript_text`.

Variáveis de auditoria da transcrição: `transcript_status`, `transcript_method`, `transcript_language`, `transcript_error`, `transcript_captured_at`.

## `processed/comments`
Uma linha por `comment_id`, incluindo comentários de primeiro nível e replies. `is_reply` diferencia os dois tipos; `parent_comment_id` liga uma reply ao comentário-pai.

## Tabelas intermediárias
- `intermediate/transcripts`: estado da coleta de transcrição por vídeo.
- `intermediate/transcript_segments`: segmentos temporais quando o método retorna timestamps.
- `intermediate/comments_status`: status por vídeo, inclusive `no_comments` e `comments_disabled`, evitando recapturas desnecessárias no modo `resume`.
