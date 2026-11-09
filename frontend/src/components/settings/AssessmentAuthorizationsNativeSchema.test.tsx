import { act, fireEvent } from '@testing-library/react';
import { assessmentAuthorizationNativeCases } from '../../utils/assessmentAuthorizationNativeCases';

assessmentAuthorizationNativeCases({
  click: async element => { await act(async () => { fireEvent.click(element); }); },
  fill: async (element, value) => { await act(async () => { fireEvent.change(element, { target: { value } }); }); },
  selectOptions: async (element, value) => { await act(async () => {
    if (Array.isArray(value)) {
      for (const option of (element as HTMLSelectElement).options) option.selected = value.includes(option.value);
      fireEvent.change(element);
    } else fireEvent.change(element, { target: { value } });
  }); },
});
