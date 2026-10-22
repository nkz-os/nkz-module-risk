---
title: "Risk & Alerts"
description: "Agronomic risk module: declarative condition engine, a 24-risk catalog, and multi-channel alert delivery."
sidebar:
  order: 1
---

# Risk & Alerts

The **agronomic risk** module for Nekazari evaluates per-parcel threats from climate, water/soil,
disease and pests, and publishes a single `Alert` entity (FIWARE Smart Data Model) delivered over
the configured channels (email, push, Zulip, webhook/N8N, Telegram).

## Features

- **Catalog of 24 risks** (frost, water stress, powdery mildew, downy mildew, red spider mite,
  fruit fly…) ready and documented.
- **Visual editor in the control panel**: any risk can be created or tuned from the UI with a
  condition tree (AND/OR/N-of-M, ranges, temporal aggregations and duration).
- **Integrated alert surface**: a global bell in the host top navigation + an «Alerts» section
  in each parcel's detail panel.
- **No direct telemetry writes**: everything flows through Orion-LD (canonical bus).

## Where alerts surface

- **Global bell** (host): badge with active high/critical alerts + dropdown.
- **Parcel details panel**: an «Alerts» section with that parcel's risks.
- **Monitor tab** of the module.

## Reference

The full technical documentation (architecture, condition DSL, data sources, complete catalog
and how to add risks) lives in the
[repo README](https://github.com/nkz-os/nkz-module-risk).
