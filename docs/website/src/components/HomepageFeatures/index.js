import clsx from 'clsx';
import Heading from '@theme/Heading';
import styles from './styles.module.css';

const FeatureList = [
  {
    title: 'Provider-Agnostic',
    icon: '\u2194\uFE0F',
    description: (
      <>
        Same agent runs on Claude, GPT, Gemini, or local Ollama models.
        Swap providers per agent, route by cost or capability. Zero vendor lock-in.
      </>
    ),
  },
  {
    title: 'StateGraph Engine',
    icon: '\u26A1',
    description: (
      <>
        Directed graph orchestration with parallel execution, conditional routing,
        human-in-the-loop, and checkpointing. Inspired by LangGraph, fully independent.
      </>
    ),
  },
  {
    title: 'Cost-Optimized',
    icon: '\uD83D\uDCB0',
    description: (
      <>
        Route simple tasks to cheap models, complex ones to frontier.
        Built-in prompt caching, context pruning, and budget controls.
      </>
    ),
  },
  {
    title: 'Multi-Agent Teams',
    icon: '\uD83E\uDD16',
    description: (
      <>
        24 specialized agents across 5 categories. Team-lead coordinates
        task decomposition with anti-stall enforcement and dynamic routing.
      </>
    ),
  },
  {
    title: 'Secure by Default',
    icon: '\uD83D\uDD12',
    description: (
      <>
        Fail-closed auth, bcrypt passwords, JWT sessions, CORS allowlist,
        SSRF protection, audit logging. Ready for production on AWS.
      </>
    ),
  },
  {
    title: 'Real-Time Dashboard',
    icon: '\uD83D\uDCCA',
    description: (
      <>
        FastAPI + WebSocket monitoring UI. Track agent interactions,
        token usage, costs, and graph execution in real time.
      </>
    ),
  },
];

function Feature({title, icon, description}) {
  return (
    <div className={clsx('col col--4', styles.featureCol)}>
      <div className="feature-card">
        <div className="feature-icon">{icon}</div>
        <Heading as="h3">{title}</Heading>
        <p>{description}</p>
      </div>
    </div>
  );
}

export default function HomepageFeatures() {
  return (
    <section className={styles.features}>
      <div className="container">
        <div className="row">
          {FeatureList.map((props, idx) => (
            <Feature key={idx} {...props} />
          ))}
        </div>
      </div>
    </section>
  );
}
