import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { Toast, ToastProvider } from '@/components/Toast';
import { RunsProvider } from '@/components/RunsProvider';
import { STATUS } from '@/api/constants';
import * as runsApi from '@/api/runs';
import * as sfApi from '@/api/salesforce';
import { SalesforcePanel } from './SalesforcePanel';
import { AssessmentConfirmView } from './assessment/AssessmentConfirmView';
import { ColumnChecklist } from './home/ColumnChecklist';

vi.mock('@/api/runs', async (importOriginal) => ({
  ...(await importOriginal()),
  listRuns: vi.fn().mockResolvedValue([]),
  getRunProfile: vi.fn(),
}));

vi.mock('@/api/salesforce', async (importOriginal) => ({
  ...(await importOriginal()),
  listAssessments: vi.fn().mockResolvedValue([]),
  createConnection: vi.fn(),
  deleteConnection: vi.fn().mockResolvedValue(null),
  listObjects: vi.fn(),
  confirmAssessmentCdes: vi.fn(),
}));

function withProviders(ui) {
  return render(
    <MemoryRouter>
      <ToastProvider>
        <RunsProvider>
          {ui}
          <Toast />
        </RunsProvider>
      </ToastProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  runsApi.listRuns.mockResolvedValue([]);
  sfApi.listAssessments.mockResolvedValue([]);
  sfApi.deleteConnection.mockResolvedValue(null);
});

describe('SalesforcePanel', () => {
  async function fillAndSubmit(user) {
    await user.type(screen.getByLabelText(/Salesforce address/), 'acme.my.salesforce.com');
    await user.type(screen.getByLabelText(/Client ID/), 'abc');
    await user.type(screen.getByLabelText(/Client secret/), 'invalid-secret');
    await user.click(screen.getByRole('button', { name: 'Connect' }));
  }

  it('shows the error, highlights the credentials, and clears the secret after a failed connect', async () => {
    const error = Object.assign(new Error('Salesforce rejected the client ID or secret.'), {
      code: 'invalid_client',
    });
    sfApi.createConnection.mockRejectedValue(error);
    const user = userEvent.setup();
    withProviders(<SalesforcePanel />);

    const secret = screen.getByLabelText(/Client secret/);
    expect(secret).toHaveAttribute('type', 'password');
    expect(secret).toHaveAttribute('autocomplete', 'off');

    await fillAndSubmit(user);

    expect(await screen.findByRole('alert')).toHaveTextContent(/rejected the client ID/);
    expect(secret).toHaveValue('');
    expect(screen.getByLabelText(/Client ID/)).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByLabelText(/Salesforce address/)).toHaveAttribute('aria-invalid', 'false');
  });

  it('clears the secret on success too and moves on to the object picker', async () => {
    sfApi.createConnection.mockResolvedValue({
      id: 'sfc_1',
      orgName: 'Acme Corporation',
      orgEdition: 'Enterprise Edition',
      username: 'integration@acme.com',
      environment: 'sandbox',
    });
    sfApi.listObjects.mockResolvedValue({
      objects: [{ name: 'Account', label: 'Account', custom: false, recordCount: 5790, suggested: true }],
      hiddenCount: 612,
    });
    const user = userEvent.setup();
    withProviders(<SalesforcePanel />);

    await fillAndSubmit(user);

    expect(await screen.findByText('Acme Corporation')).toBeInTheDocument();
    expect(screen.getByText('Sandbox')).toBeInTheDocument();
    expect(screen.queryByLabelText(/Client secret/)).not.toBeInTheDocument();
    expect(await screen.findByText(/about 5,790 records/)).toBeInTheDocument();
    // Suggested, but never pre-ticked.
    expect(screen.getByRole('checkbox', { name: /Account/ })).not.toBeChecked();
  });
});

describe('AssessmentConfirmView', () => {
  const ASSESSMENT = {
    id: 'asm_1',
    name: 'Acme Corporation',
    status: STATUS.AWAITING_CDES,
    source: { environment: 'production' },
    runs: [
      { id: 'run_a', object: 'Account', label: 'Account', status: STATUS.AWAITING_CDES },
      { id: 'run_i', object: 'Invoice__c', label: 'Invoice', status: STATUS.FAILED, error: 'Refused.' },
    ],
  };

  it('sends every awaiting object in one confirmation', async () => {
    runsApi.getRunProfile.mockResolvedValue({
      columns: [
        { name: 'Name', inferredType: 'string', fillRate: 1, distinctRatio: 1, isCde: true, cdeReason: 'x' },
        { name: 'Fax', inferredType: 'string', fillRate: 0.1, distinctRatio: 1, isCde: false, cdeReason: 'y' },
      ],
    });
    sfApi.confirmAssessmentCdes.mockResolvedValue({ id: 'asm_1', status: STATUS.PROCESSING });
    const onConfirmed = vi.fn();
    const user = userEvent.setup();
    withProviders(<AssessmentConfirmView assessment={ASSESSMENT} onConfirmed={onConfirmed} />);

    expect(await screen.findByText('Name')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Start analysis' }));

    await waitFor(() =>
      expect(sfApi.confirmAssessmentCdes).toHaveBeenCalledWith('asm_1', { Account: ['Name'] }),
    );
    expect(onConfirmed).toHaveBeenCalled();
    // The failed object is never fetched or sent.
    expect(runsApi.getRunProfile).toHaveBeenCalledTimes(1);
  });

  it("shows a failed object's error on its tab", async () => {
    runsApi.getRunProfile.mockResolvedValue({ columns: [] });
    const user = userEvent.setup();
    withProviders(<AssessmentConfirmView assessment={ASSESSMENT} />);

    await user.click(screen.getByRole('tab', { name: /Invoice/ }));
    expect(screen.getByText('Refused.')).toBeInTheDocument();
  });
});

describe('ColumnChecklist with Salesforce fields', () => {
  it('shows the label, the API name, and required / reference badges', () => {
    render(
      <ColumnChecklist
        columns={[
          {
            name: 'AccountId',
            inferredType: 'string',
            fillRate: 1,
            distinctRatio: 0.5,
            isCde: true,
            cdeReason: 'Links to the parent',
            salesforce: { label: 'Account ID', type: 'reference', required: true, referenceTo: ['Account'] },
          },
        ]}
        selected={new Set(['AccountId'])}
        onToggle={() => {}}
      />,
    );
    expect(screen.getByText('Account ID')).toBeInTheDocument();
    expect(screen.getByText('AccountId')).toBeInTheDocument();
    expect(screen.getByText('Required')).toBeInTheDocument();
    expect(screen.getByText(/→ Account/)).toBeInTheDocument();
    expect(screen.getByText('reference')).toBeInTheDocument();
  });
});
