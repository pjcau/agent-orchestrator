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
      label: 'MVP (v0.4 → v1.0)',
      collapsed: false,
      items: [
        'roadmap/v040-cooperation',
        'roadmap/v050-routing',
        'roadmap/v060-hardening',
        'roadmap/v070-graphs',
        'roadmap/v080-integrations',
        'roadmap/v100-ga',
      ],
    },
    {
      type: 'category',
      label: 'Post-MVP',
      collapsed: false,
      items: [
        'roadmap/v110-langgraph-improvements',
        'roadmap/post-mvp-scaling',
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
      ],
    },
  ],
};

export default sidebars;
