import type { AgReportState } from 'ag-studio';

const value = (id: string, label: string) => ({
  type: 'value', dataMapping: { value: [{ id, aggregation: 'sum' }] },
  format: { style: { label: { enabled: true, text: label } } }
});
const sum = (id: string) => ({ id, aggregation: 'sum' });

export const initialState = {
  selectedPageId: 'overview',
  panels: { filters: { collapsed: true } },
  pages: [
    {
      id: 'overview',
      widgets: {
        'risk-gate': { type: 'riskGate', dataMapping: {} },
        'kpi-paid': value('summary.paid', 'Paid out'),
        'kpi-blocked': value('summary.blocked', 'Blocked by the gate'),
        'kpi-waiting': value('summary.waiting', 'Waiting for you'),
        'risk-over-time': {
          type: 'line-chart', dataMapping: {
            categoryKey: [{ id: 'risk.seq' }],
            valueKey: [sum('risk.risk'), sum('risk.warn'), sum('risk.block')]
          },
          format: { title: { enabled: true, text: 'Risk over time' } }
        },
        'decisions': {
          type: 'donut-chart', dataMapping: {
            categoryKey: [{ id: 'decisions.verdict' }],
            valueKey: [{ id: 'decisions.seq', aggregation: 'count' }]
          },
          format: { title: { enabled: true, text: 'What the gate decided' } }
        },
        'paid-over-time': {
          type: 'line-chart', dataMapping: { categoryKey: [{ id: 'payments.seq' }], valueKey: [sum('payments.cumulative')] },
          format: { title: { enabled: true, text: 'Paid out over time' } }
        },
        'approvals': { type: 'approvalQueue', dataMapping: {}, format: { title: { enabled: true, text: 'Approvals waiting' } } },
        'events': {
          type: 'grid', dataMapping: {
            cols: [{ id: 'events.seq' }, { id: 'events.time' }, { id: 'events.event' }, { id: 'events.result' },
            { id: 'events.summary' }, { id: 'events.amount' }, { id: 'events.risk' }]
          },
          sort: [{ field: { id: 'events.seq' }, direction: 'desc' }], format: { title: { enabled: true, text: 'Everything the agent did' } }
        },
      },
      widgetLayout: {
        'risk-gate': { xTrack: 0, yTrack: 0, xSpan: 8, ySpan: 15 },
        'kpi-paid': { xTrack: 8, yTrack: 0, xSpan: 5, ySpan: 5 },
        'kpi-blocked': { xTrack: 13, yTrack: 0, xSpan: 5, ySpan: 5 },
        'kpi-waiting': { xTrack: 18, yTrack: 0, xSpan: 6, ySpan: 5 },
        'risk-over-time': { xTrack: 8, yTrack: 5, xSpan: 16, ySpan: 10 },
        'decisions': { xTrack: 0, yTrack: 15, xSpan: 8, ySpan: 12 },
        'paid-over-time': { xTrack: 8, yTrack: 15, xSpan: 9, ySpan: 12 },
        'approvals': { xTrack: 17, yTrack: 15, xSpan: 7, ySpan: 12 },
        'events': { xTrack: 0, yTrack: 27, xSpan: 24, ySpan: 16 },
      },
    },
    {
      id: 'products',
      widgets: {
        'gallery': { type: 'productGallery', dataMapping: {}, format: { title: { enabled: true, text: 'Products the agent saw' } } },
        'merchant-filter': { type: 'list-filter', dataMapping: { value: [{ id: 'products.merchant' }] }, format: { title: { enabled: true, text: 'Merchant' } } },
        'product-grid': {
          type: 'grid', dataMapping: {
            cols: [{ id: 'products.name' }, { id: 'products.merchant' }, { id: 'products.price' },
            { id: 'products.in_mandate' }, { id: 'products.query' }, { id: 'products.item_id' }]
          },
          format: { title: { enabled: true, text: 'Details' } }
        },
      },
      widgetLayout: {
        'gallery': { xTrack: 0, yTrack: 0, xSpan: 24, ySpan: 18 },
        'merchant-filter': { xTrack: 0, yTrack: 18, xSpan: 6, ySpan: 14 },
        'product-grid': { xTrack: 6, yTrack: 18, xSpan: 18, ySpan: 14 },
      },
    },
  ],
} as unknown as AgReportState;
