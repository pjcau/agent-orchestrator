// @ts-check

/** @type {import('@docusaurus/plugin-content-docs').SidebarsConfig} */
const sidebars = {
  architectureSidebar: [
    {
      type: 'category',
      label: 'Architecture',
      collapsed: false,
      items: [
        'architecture/overview',
        'architecture/providers',
        'architecture/agents',
        'architecture/skills',
        'architecture/graph-engine',
        'architecture/cooperation',
        'architecture/components',
      ],
    },
    {
      type: 'category',
      label: 'Guides',
      items: [
        'architecture/migration-from-claude',
      ],
    },
  ],

  roadmapSidebar: [
    'roadmap/overview',
    {
      type: 'category',
      label: 'Phases',
      collapsed: false,
      items: [
        'roadmap/phase0-aws',
        'roadmap/phase1-autonomy',
        'roadmap/phase2-revenue',
        'roadmap/phase3-maturity',
        'roadmap/post-mvp-scaling',
      ],
    },
    {
      type: 'category',
      label: 'Pre-MVP (v0.4 → v1.2)',
      collapsed: false,
      items: [
        'roadmap/v040-cooperation',
        'roadmap/v050-routing',
        'roadmap/v060-hardening',
        'roadmap/v070-graphs',
        'roadmap/v080-integrations',
        'roadmap/v100-ga',
        'roadmap/v110-langgraph-improvements',
        'roadmap/v120-dynamic-team-routing',
      ],
    },
  ],

  businessSidebar: [
    {
      type: 'category',
      label: 'Business',
      collapsed: false,
      items: [
        'business/strategy',
        'business/cost-analysis',
        'business/infrastructure',
        'business/risk-management',
        'business/growth',
        'business/financial-summary',
        'business/monitoring',
      ],
    },
  ],
};

export default sidebars;
