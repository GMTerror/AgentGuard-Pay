import type { AgDataSourcesDefinition, AgFieldDefinition } from 'ag-studio';
import type { Tables } from './tables';

const f = (id: string, name: string, format: AgFieldDefinition['format']): AgFieldDefinition => ({ id, name, format });

// Field definitions tell Studio how to format and aggregate each column. Ids are used in the report state as "<table>.<field>".
export function buildData(t: Tables): AgDataSourcesDefinition {
  return {
    sources: [
      {
        id: 'events', name: 'Events', data: t.events, fields: [f('seq', '#', 'integerFormat'), f('time', 'Time', 'textFormat'),
        f('event', 'What happened', 'textFormat'), f('result', 'Result', 'textFormat'), f('summary', 'Details', 'textFormat'),
        f('amount', 'Amount', 'decimalFormat'), f('risk', 'Risk', 'decimalFormat')]
      },
      {
        id: 'decisions', name: 'Gate decisions', data: t.decisions, fields: [f('seq', '#', 'integerFormat'), f('action', 'Action', 'textFormat'),
        f('verdict', 'Verdict', 'textFormat'), f('reason', 'Reason', 'textFormat'), f('amount', 'Amount', 'decimalFormat'),
        f('risk', 'Risk', 'decimalFormat'), f('drift', 'Intent drift', 'decimalFormat')]
      },
      {
        id: 'payments', name: 'Payments', data: t.payments, fields: [f('seq', 'Event number', 'integerFormat'), f('order_id', 'Order', 'textFormat'),
        f('amount', 'Amount', 'decimalFormat'), f('cumulative', 'Paid out so far', 'decimalFormat')]
      },
      {
        id: 'risk', name: 'Risk', data: t.risk, fields: [f('seq', 'Event number', 'integerFormat'), f('risk', 'Risk', 'decimalFormat'),
        f('warn', 'Approvals required at', 'decimalFormat'), f('block', 'Read-only at', 'decimalFormat')]
      },
      {
        id: 'products', name: 'Products seen', data: t.products, fields: [f('item_id', 'Item id', 'textFormat'), f('name', 'Product', 'textFormat'),
        f('merchant', 'Merchant', 'textFormat'), f('price', 'Price', 'decimalFormat'), f('image', 'Image', 'textFormat'),
        f('query', 'Found by search', 'textFormat'), f('in_mandate', 'In mandate', 'textFormat')]
      },
      {
        id: 'summary', name: 'Summary', data: t.summary, fields: [f('id', 'Id', 'textFormat'), f('paid', 'Paid out', 'decimalFormat'),
        f('blocked', 'Blocked', 'integerFormat'), f('waiting', 'Waiting for you', 'integerFormat'), f('events', 'Events', 'integerFormat'),
        f('risk', 'Risk', 'decimalFormat'), f('limit', 'Limit', 'decimalFormat')]
      },
    ],
  };
}
