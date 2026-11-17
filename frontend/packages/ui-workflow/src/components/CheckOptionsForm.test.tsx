import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { vi } from 'vitest';
import { ApmeApiProvider } from '../api/apmeApiAdapter';
import { CheckOptionsForm } from './CheckOptionsForm';

it('keeps the version selector and supports custom Ansible Core versions', async () => {
  const onAnsibleVersionChange = vi.fn();
  const fetch = vi.fn(async () => Response.json([]));
  render(
    <ApmeApiProvider adapter={{ fetch }}>
      <CheckOptionsForm
        ansibleVersion=""
        onAnsibleVersionChange={onAnsibleVersionChange}
        collections=""
        onCollectionsChange={vi.fn()}
        enableAi={false}
        onEnableAiChange={vi.fn()}
      />
    </ApmeApiProvider>,
  );
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

  fireEvent.click(screen.getByRole('button', { name: 'Advanced Options' }));
  const versionSelect = screen.getByLabelText('Ansible Core Version');
  fireEvent.click(versionSelect);
  fireEvent.click(
    await screen.findByRole('option', { name: 'Custom version…' }),
  );
  const customInput = screen.getByRole('textbox', {
    name: 'Custom Ansible Core version',
  });
  fireEvent.change(customInput, { target: { value: '2.16.3' } });

  expect(onAnsibleVersionChange).toHaveBeenLastCalledWith('2.16.3');
  expect(within(versionSelect).getByText('Custom version…')).toBeTruthy();
});
