import { useState, useEffect } from 'react';
import {
  Search,
  Train,
  Clock,
  MapPin,
  Map,
  Info,
  ChevronRight,
  Navigation,
  Radio,
} from 'lucide-react';
import * as api from '../services/api';
import { wsService } from '../services/websocket';
import { ETAPrediction } from '../types';

const PassengerView = () => {
  const [search, setSearch] = useState('');
  const [searching, setSearching] = useState(false);
  const [selectedTrain, setSelectedTrain] = useState<any>(null);
  const [eta, setEta] = useState<ETAPrediction[]>([]);
  const [route, setRoute] = useState<any[]>([]);
  const [lastUpdate, setLastUpdate] = useState(0);

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!search.trim()) return;

    setSearching(true);

    try {
      const trains = await api.getTrains(search.trim());

      if (trains.length > 0) {
        const train = trains[0];

        const [position, etas, trainRoute] = await Promise.all([
          api.getTrainPosition(train.train_id).catch(() => null),
          api.getTrainETA(train.train_id).catch(() => []),
          api.getTrainRoute(train.train_id).catch(() => []),
        ]);

        setSelectedTrain({
          ...train,
          position,
        });

        setEta(etas);
        setRoute(trainRoute);
        setLastUpdate(0);
      } else {
        setSelectedTrain(null);
        setEta([]);
        setRoute([]);
      }
    } catch (error) {
      console.error(error);
      setSelectedTrain(null);
    }

    setSearching(false);
  };

  useEffect(() => {
    if (!selectedTrain) return;

    const unsub = wsService.onETAUpdate((data) => {
      if (data.train_id === selectedTrain.train_id) {
        setEta(data.predictions);
        setLastUpdate(0);
      }
    });

    return () => unsub();
  }, [selectedTrain]);

  useEffect(() => {
    if (!selectedTrain) return;

    const interval = setInterval(() => {
      setLastUpdate((prev) => prev + 1);
    }, 1000);

    return () => clearInterval(interval);
  }, [selectedTrain]);

  const nextStation =
    eta.length > 0
      ? eta[0]
      : route.length > 0
        ? route[0]
        : null;

  const finalStation =
    eta.length > 0
      ? eta[eta.length - 1]
      : route.length > 0
        ? route[route.length - 1]
        : null;

  const formatTime = (timeStr: string) => {
    if (!timeStr) return '--:--';

    if (timeStr.includes(':') && timeStr.length <= 5) {
      return timeStr;
    }

    try {
      const date = new Date(timeStr);

      if (isNaN(date.getTime())) {
        return timeStr;
      }

      return date.toLocaleTimeString([], {
        hour: '2-digit',
        minute: '2-digit',
      });
    } catch {
      return timeStr;
    }
  };

  const delay = nextStation?.predicted_delay_minutes || 0;

  const getDelayStyles = () => {
    if (delay > 15) {
      return {
        badge: 'border-red-200 bg-red-50 text-red-700',
        dot: 'bg-red-500',
        label: `${delay} min late`,
      };
    }

    if (delay > 0) {
      return {
        badge: 'border-amber-200 bg-amber-50 text-amber-700',
        dot: 'bg-amber-500',
        label: `${delay} min late`,
      };
    }

    return {
      badge: 'border-emerald-200 bg-emerald-50 text-emerald-700',
      dot: 'bg-emerald-500',
      label: 'On Time',
    };
  };

  const delayStyles = getDelayStyles();

  return (
    <div className="flex flex-col gap-6 pb-8">

      {/* PAGE HEADER */}
      <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">

        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-red-700">
            Passenger Information
          </p>

          <div className="mt-1 flex items-center gap-3">
            <h1 className="text-3xl font-bold tracking-tight text-slate-900">
              Track Your Train
            </h1>

            <span className="inline-flex items-center gap-2 rounded-full border border-blue-200 bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-700">
              <Radio className="h-3 w-3" />
              ETA Forecast
            </span>
          </div>

          <p className="mt-2 text-sm text-slate-500">
            Search a train to view its dynamic arrival forecast and route timetable
          </p>
        </div>

        <div className="hidden items-center gap-2 rounded-lg border border-slate-200 bg-white px-4 py-3 shadow-sm lg:flex">
          <Train className="h-4 w-4 text-blue-600" />

          <div>
            <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
              Data Mode
            </p>

            <p className="text-sm font-semibold text-slate-700">
              Timetable + Simulated Telemetry
            </p>
          </div>
        </div>
      </div>

      {/* SEARCH PANEL */}
      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">

        <form
          onSubmit={handleSearch}
          className="flex flex-col gap-3 md:flex-row"
        >
          <div className="relative flex-1">
            <Search className="absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-slate-400" />

            <input
              type="text"
              placeholder="Enter train number or train name..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="h-12 w-full rounded-lg border border-slate-200 bg-slate-50 pl-12 pr-4 text-sm text-slate-900 outline-none transition focus:border-red-300 focus:bg-white focus:ring-2 focus:ring-red-100"
            />
          </div>

          <button
            type="submit"
            disabled={searching}
            className="flex h-12 items-center justify-center gap-2 rounded-lg bg-slate-900 px-6 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {searching ? (
              <>
                <div className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" />
                Searching...
              </>
            ) : (
              <>
                <Search className="h-4 w-4" />
                Search Train
              </>
            )}
          </button>
        </form>

        {/* QUICK SEARCH */}
        <div className="mt-4 flex flex-wrap items-center gap-2 text-xs">
          <span className="font-medium text-slate-400">
            Quick search:
          </span>

          <button
            type="button"
            onClick={() => setSearch('20491')}
            className="rounded-md border border-slate-200 bg-slate-50 px-3 py-1.5 font-semibold text-slate-600 transition hover:border-red-200 hover:bg-red-50 hover:text-red-700"
          >
            20491 · Jaisalmer SF
          </button>

          <button
            type="button"
            onClick={() => setSearch('12951')}
            className="rounded-md border border-slate-200 bg-slate-50 px-3 py-1.5 font-semibold text-slate-600 transition hover:border-red-200 hover:bg-red-50 hover:text-red-700"
          >
            12951 · Mumbai Rajdhani
          </button>

          <button
            type="button"
            onClick={() => setSearch('22436')}
            className="rounded-md border border-slate-200 bg-slate-50 px-3 py-1.5 font-semibold text-slate-600 transition hover:border-red-200 hover:bg-red-50 hover:text-red-700"
          >
            22436 · Vande Bharat
          </button>
        </div>
      </div>

      {/* EMPTY STATE */}
      {!selectedTrain && (
        <div className="flex min-h-[420px] flex-col items-center justify-center rounded-xl border border-slate-200 bg-white shadow-sm">

          <div className="flex h-16 w-16 items-center justify-center rounded-full bg-slate-100">
            <Train className="h-8 w-8 text-slate-400" />
          </div>

          <h2 className="mt-5 text-xl font-bold text-slate-900">
            Search for a train
          </h2>

          <p className="mt-2 max-w-md text-center text-sm text-slate-500">
            Enter a train number or name above to view its current position,
            AI-powered ETA forecast and complete timetable.
          </p>

        </div>
      )}

      {/* TRAIN RESULT */}
      {selectedTrain && (
        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">

          {/* TRAIN HEADER */}
          <div className="border-b border-slate-200 p-6">

            <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">

              <div className="min-w-0">

                <div className="flex flex-wrap items-center gap-2">

                  <span className="rounded-md bg-slate-100 px-2.5 py-1 text-xs font-bold text-slate-700">
                    {selectedTrain.train_number}
                  </span>

                  <span className="rounded-md bg-blue-50 px-2.5 py-1 text-xs font-semibold text-blue-700">
                    {selectedTrain.train_type || 'Superfast'}
                  </span>

                  <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-emerald-700">
                    <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                    Master Data
                  </span>

                </div>

                <h2 className="mt-3 text-2xl font-bold text-slate-900">
                  {selectedTrain.train_name}
                </h2>

                <div className="mt-3 flex flex-wrap items-center gap-3 text-sm text-slate-600">

                  <div className="flex items-center gap-2">
                    <MapPin className="h-4 w-4 text-red-500" />
                    {selectedTrain.source}
                  </div>

                  <ChevronRight className="h-4 w-4 text-slate-300" />

                  <div className="flex items-center gap-2">
                    <Map className="h-4 w-4 text-slate-400" />
                    {selectedTrain.destination}
                  </div>

                </div>

              </div>

              <span
                className={`inline-flex shrink-0 items-center gap-2 rounded-full border px-4 py-2 text-sm font-semibold ${delayStyles.badge}`}
              >
                <span
                  className={`h-2 w-2 rounded-full ${delayStyles.dot}`}
                />
                {delayStyles.label}
              </span>

            </div>

            {/* TRAIN META */}
            <div className="mt-6 grid grid-cols-2 gap-4 border-t border-slate-100 pt-5 md:grid-cols-4">

              <div>
                <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                  Runs
                </p>

                <p className="mt-1 text-sm font-semibold text-slate-700">
                  {selectedTrain.days_of_run || 'Daily'}
                </p>
              </div>

              <div>
                <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                  Distance
                </p>

                <p className="mt-1 text-sm font-semibold text-slate-700">
                  {selectedTrain.total_distance_km
                    ? `${selectedTrain.total_distance_km} km`
                    : '—'}
                </p>
              </div>

              <div>
                <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                  Stops
                </p>

                <p className="mt-1 text-sm font-semibold text-slate-700">
                  {route.length} stations
                </p>
              </div>

              <div>
                <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                  Last Update
                </p>

                <p className="mt-1 text-sm font-semibold text-slate-700">
                  {lastUpdate === 0
                    ? 'Just now'
                    : `${lastUpdate}s ago`}
                </p>
              </div>

            </div>
          </div>

          {/* TELEMETRY NOTICE */}
          <div className="flex flex-col gap-2 border-b border-amber-100 bg-amber-50 px-6 py-3 text-xs text-amber-900 sm:flex-row sm:items-center sm:justify-between">

            <div className="flex items-center gap-2">
              <span className="h-2 w-2 animate-pulse rounded-full bg-amber-500" />

              <span>
                <strong>Telemetry:</strong> Simulated
                <span className="mx-1.5 text-amber-400">•</span>
                No authorized live railway GPS feed
              </span>
            </div>

            <span className="font-semibold text-blue-700">
              ETA: AI Forecast
            </span>

          </div>

          {/* ETA SECTION */}
          {nextStation && (
            <div className="border-b border-slate-200 p-6 md:p-8">

              <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">

                {/* NEXT STATION */}
                <div className="rounded-xl border border-slate-200 bg-slate-50 p-6 lg:col-span-2">

                  <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
                    <MapPin className="h-4 w-4 text-red-500" />
                    Next Stop
                  </div>

                  <h3 className="mt-3 text-3xl font-bold text-slate-900">
                    {nextStation.station_name}
                  </h3>

                  <div className="mt-6 flex flex-col gap-6 sm:flex-row sm:items-end">

                    <div>
                      <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                        Scheduled Arrival
                      </p>

                      <p className="mt-2 text-xl font-medium text-slate-400 line-through">
                        {formatTime(
                          nextStation.scheduled_arrival ||
                          nextStation.arrival
                        )}
                      </p>
                    </div>

                    <ChevronRight className="hidden h-6 w-6 text-slate-300 sm:block" />

                    <div>
                      <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-blue-600">
                        <span className="h-2 w-2 animate-pulse rounded-full bg-blue-500" />
                        AI Predicted Arrival
                      </p>

                      <p className="mt-1 text-4xl font-black tracking-tight text-slate-900">
                        {formatTime(
                          nextStation.predicted_arrival ||
                          nextStation.arrival
                        )}
                      </p>
                    </div>

                  </div>

                </div>

                {/* DELAY / POSITION */}
                <div className="rounded-xl border border-slate-200 bg-white p-6">

                  <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
                    <Clock className="h-4 w-4 text-amber-500" />
                    Forecast
                  </div>

                  <div className="mt-4">
                    <p className="text-4xl font-black text-slate-900">
                      {delay > 0 ? `${delay} min` : 'On Time'}
                    </p>

                    <p className="mt-2 text-sm text-slate-500">
                      Expected delay at next station
                    </p>
                  </div>

                  {selectedTrain.position && (
                    <div className="mt-6 border-t border-slate-100 pt-5">

                      <div className="flex items-center justify-between">
                        <span className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
                          <Navigation className="h-4 w-4 text-blue-500" />
                          Journey Progress
                        </span>

                        <span className="text-sm font-bold text-slate-700">
                          {Math.round(
                            selectedTrain.position.journey_progress || 0
                          )}%
                        </span>
                      </div>

                      <div className="mt-3 h-2.5 overflow-hidden rounded-full bg-slate-100">
                        <div
                          className="h-full rounded-full bg-slate-900 transition-all duration-1000"
                          style={{
                            width: `${Math.min(
                              100,
                              Math.max(
                                0,
                                selectedTrain.position.journey_progress || 0
                              )
                            )}%`,
                          }}
                        />
                      </div>

                    </div>
                  )}

                </div>

              </div>

              {/* DESTINATION FORECAST */}
              {finalStation && finalStation !== nextStation && (
                <div className="mt-5 flex flex-col gap-4 rounded-xl border border-slate-200 bg-white p-5 sm:flex-row sm:items-center sm:justify-between">

                  <div>
                    <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                      Destination Forecast
                    </p>

                    <p className="mt-1 font-bold text-slate-800">
                      {finalStation.station_name}
                    </p>

                    <p className="mt-1 text-sm text-slate-500">
                      Scheduled:{' '}
                      {formatTime(
                        finalStation.scheduled_arrival ||
                        finalStation.arrival
                      )}
                    </p>
                  </div>

                  <div className="sm:text-right">

                    <p className="text-xs font-semibold uppercase tracking-wider text-blue-600">
                      AI Forecast
                    </p>

                    <p className="mt-1 text-2xl font-black text-slate-900">
                      {formatTime(
                        finalStation.predicted_arrival ||
                        finalStation.arrival
                      )}
                    </p>

                  </div>

                </div>
              )}

            </div>
          )}

          {/* FULL ROUTE */}
          {route.length > 0 && (
            <div className="border-b border-slate-200 p-6">

              <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">

                <div>
                  <h3 className="text-lg font-bold text-slate-900">
                    Full Route Timetable
                  </h3>

                  <p className="mt-1 text-xs text-slate-400">
                    {route.length} stations
                  </p>
                </div>

                <span className="text-xs text-slate-400">
                  Source: {selectedTrain.data_source || 'Real Train Master'}
                </span>

              </div>

              <div className="mt-5 max-h-[420px] overflow-y-auto rounded-lg border border-slate-200">

                {route.map((station, index) => (
                  <div
                    key={index}
                    className="flex flex-col gap-4 border-b border-slate-100 px-4 py-4 last:border-b-0 hover:bg-slate-50 md:flex-row md:items-center md:justify-between"
                  >

                    <div className="flex min-w-0 items-center gap-4">

                      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-slate-100 text-xs font-bold text-slate-500">
                        {station.stop_number || index + 1}
                      </div>

                      <div className="min-w-0">

                        <p className="truncate font-semibold text-slate-800">
                          {station.station_name}

                          {station.station_code && (
                            <span className="ml-2 text-xs font-mono font-normal text-slate-400">
                              ({station.station_code})
                            </span>
                          )}
                        </p>

                        <p className="mt-1 text-xs text-slate-400">
                          Day {station.day || 1}
                          <span className="mx-1.5">•</span>
                          {station.distance_from_source !== null &&
                          station.distance_from_source !== undefined
                            ? `${station.distance_from_source} km`
                            : station.distance !== null &&
                                station.distance !== undefined
                              ? `${station.distance} km`
                              : 'Distance unavailable'}
                        </p>

                      </div>

                    </div>

                    <div className="md:text-right">

                      <p className="text-sm font-medium text-slate-700">
                        Arr: {formatTime(station.arrival)}
                        <span className="mx-1 text-slate-300">|</span>
                        Dep: {formatTime(station.departure)}
                      </p>

                      {station.halt_minutes ? (
                        <p className="mt-1 text-xs font-semibold text-blue-600">
                          {station.halt_minutes} min halt
                        </p>
                      ) : null}

                    </div>

                  </div>
                ))}

              </div>

            </div>
          )}

          {/* AI REASONING */}
          {nextStation &&
            nextStation.predicted_delay_minutes > 0 &&
            nextStation.factors?.[0] && (
              <div className="border-b border-slate-200 bg-blue-50/50 p-6">

                <div className="flex items-start gap-4">

                  <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-blue-100 text-blue-600">
                    <Info className="h-5 w-5" />
                  </div>

                  <div>

                    <h3 className="font-bold text-slate-900">
                      Why is the ETA different from the schedule?
                    </h3>

                    <p className="mt-2 text-sm leading-6 text-slate-600">
                      The ETA model has analyzed the current operational
                      conditions and forecast a{' '}
                      <strong>
                        {nextStation.predicted_delay_minutes} minute
                      </strong>{' '}
                      delay, primarily influenced by{' '}
                      <span className="font-semibold text-blue-700">
                        {nextStation.factors[0].factor_name.toLowerCase()}
                      </span>
                      .
                    </p>

                  </div>

                </div>

              </div>
            )}

          {/* FOOTER */}
          <div className="flex flex-col items-center justify-center gap-2 bg-slate-50 px-6 py-4 text-center text-xs text-slate-400 sm:flex-row">

            <Clock className="h-3.5 w-3.5" />

            <span>
              Timetable from railway master data
              <span className="mx-1.5">•</span>
              Telemetry simulated for SIH demonstration
            </span>

          </div>

        </div>
      )}

    </div>
  );
};

export default PassengerView;