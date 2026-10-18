import { useEffect, useLayoutEffect, useRef } from 'react';
import { useInRouterContext, useLocation } from 'react-router-dom';

type RouterSearchListenerProps = {
  onSearchChange: (search: string) => void;
};

function RouterSearchSubscriber({ onSearchChange }: RouterSearchListenerProps) {
  const { search } = useLocation();
  const callbackRef = useRef(onSearchChange);
  useLayoutEffect(() => {
    callbackRef.current = onSearchChange;
  });
  useEffect(() => {
    callbackRef.current(search);
  }, [search]);
  return null;
}

/**
 * Reports the router's query string on mount and whenever it changes. A list
 * page that keeps its filters in local state renders this to follow URL
 * changes it did not make itself, such as the nav link or Back/Forward.
 * Outside a router (a page rendered on its own in a unit test) it renders
 * nothing.
 */
export function RouterSearchListener({ onSearchChange }: RouterSearchListenerProps) {
  return useInRouterContext() ? <RouterSearchSubscriber onSearchChange={onSearchChange} /> : null;
}
