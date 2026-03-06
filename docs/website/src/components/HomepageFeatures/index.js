import clsx from 'clsx';
import Heading from '@theme/Heading';
import styles from './styles.module.css';

const FeatureList = [
  {
    title: 'Provider-Agnostic',
    description: (
      <>
        Same agent runs on Claude, GPT, Gemini, or local Ollama models.
        Swap providers per agent, route by cost or capability.
      </>
    ),
  },
  {
    title: 'StateGraph Engine',
    description: (
      <>
        Directed graph orchestration with parallel execution, conditional routing,
        human-in-the-loop, and checkpointing. Inspired by LangGraph, fully independent.
      </>
    ),
  },
  {
    title: 'Cost-Optimized',
    description: (
      <>
        Route simple tasks to cheap models, complex ones to frontier.
        Built-in prompt caching, context pruning, and budget controls.
      </>
    ),
  },
];

function Feature({title, description}) {
  return (
    <div className={clsx('col col--4')}>
      <div className="text--center padding-horiz--md padding-vert--md">
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
