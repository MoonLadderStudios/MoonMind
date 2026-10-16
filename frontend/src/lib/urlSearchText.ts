import { useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

/**
 * Text input state mirrored into one URL query parameter.
 *
 * React Router commits navigations as transitions, so an input whose value is
 * read back from the URL is reset between fast keystrokes. The typed value
 * stays local and urgent; the URL follows with `replace`. URL changes this
 * input did not make (links, history) still replace the local value.
 */
export function useUrlSearchText(
  key: string,
  clearKeys: readonly string[] = [],
): [string, (next: string) => void] {
  const location = useLocation();
  const navigate = useNavigate();
  const urlValue = new URLSearchParams(location.search).get(key) ?? '';
  const [value, setValue] = useState(urlValue);
  const pendingWrites = useRef<string[]>([]);

  useEffect(() => {
    const ownWrite = pendingWrites.current.indexOf(urlValue);
    if (ownWrite >= 0) {
      pendingWrites.current.splice(0, ownWrite + 1);
      return;
    }
    pendingWrites.current = [];
    setValue(urlValue);
  }, [urlValue]);

  const update = (next: string) => {
    setValue(next);
    pendingWrites.current.push(next);
    const search = new URLSearchParams(location.search);
    if (next) search.set(key, next);
    else search.delete(key);
    for (const clearKey of clearKeys) search.delete(clearKey);
    navigate({ pathname: location.pathname, search: search.toString() }, { replace: true });
  };

  return [value, update];
}
