/**
 * Card registration entry point.
 *
 * Imports all card web components and registers them in the
 * Home Assistant Lovelace card picker (window.customCards).
 */

import './summary-card'
import './consumption-card'
import './consumption-breakdown-card'
import './cost-card'
import './reading-card'
import './ev-card'

// Extend the global Window type for HA card registration
declare global {
  interface Window {
    customCards?: Array<{
      type: string
      name: string
      description: string
      preview?: boolean
    }>
  }
}

window.customCards = window.customCards || []

window.customCards.push(
  {
    type: 'eon-next-fork-summary-card',
    name: 'EON Next Fork Summary',
    description:
      'Compact overview of your EON Next Fork energy data including consumption, costs, and EV charging status.',
    preview: true
  },
  {
    type: 'eon-next-fork-consumption-card',
    name: 'EON Next Fork Consumption',
    description:
      'Consumption chart and daily usage for a single meter with 7-day history.',
    preview: true
  },
  {
    type: 'eon-next-fork-consumption-breakdown-card',
    name: 'EON Next Fork Cost Breakdown',
    description:
      'Pie chart showing usage charges vs standing charges, plus tracker-powered tracked/untracked usage split for a single meter.',
    preview: true
  },
  {
    type: 'eon-next-fork-cost-card',
    name: 'EON Next Fork Costs',
    description:
      'Cost summary for a single meter showing today, yesterday, standing charge, and unit rate.',
    preview: true
  },
  {
    type: 'eon-next-fork-reading-card',
    name: 'EON Next Fork Meter Reading',
    description: 'Latest meter reading, date, and tariff information for a single meter.',
    preview: true
  },
  {
    type: 'eon-next-fork-ev-card',
    name: 'EON Next Fork EV Charging',
    description:
      'Smart charging schedule timeline showing upcoming charge slots for your EV.',
    preview: true
  }
)
