---
title: "Riesgos y Avisos"
description: "Módulo de riesgos agronómicos: motor de condiciones declarativo, catálogo de 24 riesgos y entrega multicanal de avisos."
sidebar:
  order: 1
---

# Riesgos y Avisos

El módulo de **riesgos agronómicos** de Nekazari evalúa por parcela las amenazas de clima,
agua/suelo, enfermedades y plagas, y publica una única entidad `Alert` (FIWARE Smart Data Model)
que se entrega por los canales configurados (email, push, Zulip, webhook/N8N, Telegram).

## Características

- **Catálogo de 24 riesgos** (helada, estrés hídrico, oídio, mildiu, araña roja, mosca de la
  fruta…), listo y documentado.
- **Panel de control con editor visual**: cualquier riesgo se puede crear o ajustar desde la
  UI con un árbol de condiciones (AND/OR/N-de-M, rangos, agregaciones temporales y duración).
- **Superficie de avisos integrada**: campana global en la barra de navegación + sección
  «Avisos» en el panel de cada parcela.
- **Sin escrituras directas a telemetría**: todo fluye por Orion-LD (bus canónico).

## Dónde se ven los avisos

- **Campana global** (host): badge con los avisos activos high/critical + desplegable.
- **Panel de detalle de parcela**: sección «Avisos» con los riesgos de esa parcela.
- **Pestaña Monitor** del módulo.

## Referencia

La documentación técnica completa (arquitectura, DSL de condiciones, fuentes de datos,
catálogo completo y cómo añadir riesgos) está en el [README del repositorio](https://github.com/nkz-os/nkz-module-risk).
