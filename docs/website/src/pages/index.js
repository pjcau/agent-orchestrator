import clsx from 'clsx';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Layout from '@theme/Layout';
import HomepageFeatures from '@site/src/components/HomepageFeatures';

import Heading from '@theme/Heading';
import styles from './index.module.css';

function HomepageHeader() {
  const {siteConfig} = useDocusaurusContext();
  return (
    <header className={clsx('hero hero--primary', styles.heroBanner)}>
      <div className="container">
        <div className={styles.heroLogo}>
          <img src="/agent-orchestrator/img/logo.svg" alt="Agent Orchestrator" width="120" height="120" />
        </div>
        <Heading as="h1" className="hero__title">
          {siteConfig.title}
        </Heading>
        <p className="hero__subtitle">{siteConfig.tagline}</p>
        <div className={styles.buttons}>
          <Link
            className="button button--secondary button--lg"
            to="/docs/architecture/overview">
            Get Started
          </Link>
          <Link
            className={clsx('button button--lg', styles.buttonOutline)}
            to="/docs/roadmap/overview">
            Roadmap
          </Link>
        </div>

        {/* Stats bar */}
        <div className="stats-bar">
          <div className="stat-item">
            <div className="stat-value">24</div>
            <div className="stat-label">Agents</div>
          </div>
          <div className="stat-item">
            <div className="stat-value">5</div>
            <div className="stat-label">Providers</div>
          </div>
          <div className="stat-item">
            <div className="stat-value">6</div>
            <div className="stat-label">Routing Strategies</div>
          </div>
          <div className="stat-item">
            <div className="stat-value">11</div>
            <div className="stat-label">Skills</div>
          </div>
        </div>
      </div>
    </header>
  );
}

export default function Home() {
  const {siteConfig} = useDocusaurusContext();
  return (
    <Layout
      title="Home"
      description="Provider-agnostic AI agent orchestration framework">
      <HomepageHeader />
      <main>
        <HomepageFeatures />
      </main>
    </Layout>
  );
}
