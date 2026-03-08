// @ts-check
import {themes as prismThemes} from 'prism-react-renderer';

/** @type {import('@docusaurus/types').Config} */
const config = {
  title: 'Agent Orchestrator',
  tagline: 'Provider-agnostic AI agent orchestration framework',
  favicon: 'img/favicon.ico',

  future: {
    v4: true,
  },

  markdown: {
    mermaid: true,
  },
  themes: ['@docusaurus/theme-mermaid'],

  url: 'https://pjcau.github.io',
  baseUrl: '/agent-orchestrator/',

  organizationName: 'pjcau',
  projectName: 'agent-orchestrator',
  deploymentBranch: 'gh-pages',
  trailingSlash: false,

  onBrokenLinks: 'throw',

  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  presets: [
    [
      'classic',
      /** @type {import('@docusaurus/preset-classic').Options} */
      ({
        docs: {
          sidebarPath: './sidebars.js',
          editUrl:
            'https://github.com/pjcau/agent-orchestrator/tree/main/docs/website/',
        },
        blog: false,
        theme: {
          customCss: './src/css/custom.css',
        },
      }),
    ],
  ],

  themeConfig:
    /** @type {import('@docusaurus/preset-classic').ThemeConfig} */
    ({
      colorMode: {
        respectPrefersColorScheme: true,
      },
      navbar: {
        title: 'Agent Orchestrator',
        items: [
          {
            type: 'docSidebar',
            sidebarId: 'architectureSidebar',
            position: 'left',
            label: 'Architecture',
          },
          {
            type: 'docSidebar',
            sidebarId: 'roadmapSidebar',
            position: 'left',
            label: 'Roadmap',
          },
          {
            type: 'docSidebar',
            sidebarId: 'businessSidebar',
            position: 'left',
            label: 'Business',
          },
          {
            href: 'https://github.com/pjcau/agent-orchestrator',
            label: 'GitHub',
            position: 'right',
          },
        ],
      },
      footer: {
        style: 'dark',
        links: [
          {
            title: 'Documentation',
            items: [
              {label: 'Architecture', to: '/docs/architecture/overview'},
              {label: 'Roadmap', to: '/docs/roadmap/overview'},
              {label: 'Business', to: '/docs/business/strategy'},
            ],
          },
          {
            title: 'More',
            items: [
              {
                label: 'GitHub',
                href: 'https://github.com/pjcau/agent-orchestrator',
              },
            ],
          },
        ],
        copyright: `Copyright ${new Date().getFullYear()} Agent Orchestrator. Built with Docusaurus.`,
      },
      prism: {
        theme: prismThemes.github,
        darkTheme: prismThemes.dracula,
        additionalLanguages: ['python', 'yaml', 'bash', 'docker'],
      },
    }),
};

export default config;
