# when implementing run A and B in parallel, then C, then D, then E.

┌─────────────────────────────────────────────────────┐
│  SHARED CONTEXT BLOCK                                │
│  Competition rules, universe, scoring, architecture  │
│  summary — prepended to every implementor prompt     │
└─────────────────────────────────────────────────────┘
         ↓ prepend to each of the following ↓

┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│ IMPLEMENTOR A  │  │ IMPLEMENTOR B   │  │ IMPLEMENTOR C  │
│ Data & Features│  │ Regime & Signals│  │ Portfolio & Risk│
│ Layers 1–2     │  │ Layers 3–4      │  │ Layers 5–6     │
└──────────────────┘  └──────────────────┘  └──────────────────┘

┌──────────────────┐  ┌──────────────────┐
│ IMPLEMENTOR D  │  │ IMPLEMENTOR E   │
│ Execution &    │  │ Orchestration   │
│ Adaptation     │  │ Main Loop + Glue│
│ Layers 7–8     │  │ All layers      │
└──────────────────┘  └──────────────────┘