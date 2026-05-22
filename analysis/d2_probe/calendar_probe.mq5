//+------------------------------------------------------------------+
//|  calendar_probe.mq5                                              |
//|                                                                  |
//|  D2 (news-surprise drift) DATA-FEASIBILITY PROBE.                |
//|                                                                  |
//|  Question it answers: does the MT5 built-in economic calendar    |
//|  carry, for our 8-instrument universe over 2023-05 -> 2026-05:   |
//|    (1) enough DEPTH (earliest event <= 2023-05),                 |
//|    (2) a non-null CONSENSUS forecast on high-impact events       |
//|        (so surprise = actual - consensus is computable),         |
//|    (3) the RIGHT events (NFP/CPI/FOMC/ECB/BoE/... present).      |
//|                                                                  |
//|  It writes NOTHING and trades NOTHING -- read-only diagnostic.   |
//|  Run as a Script in the Capital.com-Demo MT5 terminal; results   |
//|  print to Toolbox -> Experts tab. Copy that output back.         |
//|                                                                  |
//|  Note: calendar event times are in the terminal/server timezone; |
//|  UTC alignment to the OHLC bars is a BUILD-time fidelity check,  |
//|  deferred until this depth/coverage gate passes.                 |
//+------------------------------------------------------------------+
#property script_show_inputs
#property strict

input datetime InpFrom = D'2023.05.01 00:00';   // window start (audit re-baseline)
input datetime InpTo   = D'2026.05.22 00:00';   // window end (today)

// ISO 3166-1 alpha-2 country codes covering the universe:
//   US -> US500, USD pairs, GOLD ; EU -> ECB/eurozone aggregate, DE40, EUR ;
//   DE -> German-specific (IFO/ZEW/German CPI), DE40 ; GB -> UK100, GBP ;
//   JP -> JPY (USDJPY)
string Countries[] = {"US","EU","DE","GB","JP"};

void OnStart()
  {
   PrintFormat("=== D2 calendar probe  %s -> %s ===",
               TimeToString(InpFrom,TIME_DATE), TimeToString(InpTo,TIME_DATE));
   PrintFormat("%-4s | %-11s | %7s | %8s | %11s | %14s",
               "ctry","earliest","events","high-imp","%cons(all)","%cons(high)");

   for(int c=0; c<ArraySize(Countries); c++)
     {
      MqlCalendarValue values[];
      int n = CalendarValueHistory(values, InpFrom, InpTo, Countries[c]);

      if(n <= 0)
        {
         PrintFormat("%-4s | %-11s | %7d | %8s | %11s | %14s   (no data, err=%d)",
                     Countries[c], "-", 0, "-", "-", "-", GetLastError());
         continue;
        }

      datetime earliest = D'2100.01.01';
      int consAll = 0, highCnt = 0, consHigh = 0;
      string sampleNames[];   // up to 12 distinct high-importance event names

      for(int i=0; i<n; i++)
        {
         if(values[i].time < earliest)
            earliest = values[i].time;

         // LONG_MIN is the calendar's "no value" sentinel for the field
         bool hasForecast = (values[i].forecast_value != LONG_MIN);
         if(hasForecast)
            consAll++;

         MqlCalendarEvent ev;
         if(CalendarEventById(values[i].event_id, ev))
           {
            if(ev.importance == CALENDAR_IMPORTANCE_HIGH)
              {
               highCnt++;
               if(hasForecast)
                  consHigh++;

               // collect distinct names so we can eyeball the coverage
               if(ArraySize(sampleNames) < 12)
                 {
                  bool seen = false;
                  for(int s=0; s<ArraySize(sampleNames); s++)
                     if(sampleNames[s] == ev.name) { seen = true; break; }
                  if(!seen)
                    {
                     int sz = ArraySize(sampleNames);
                     ArrayResize(sampleNames, sz+1);
                     sampleNames[sz] = ev.name;
                    }
                 }
              }
           }
        }

      double pctAll  = (n > 0)       ? 100.0 * consAll  / n       : 0.0;
      double pctHigh = (highCnt > 0) ? 100.0 * consHigh / highCnt : 0.0;

      PrintFormat("%-4s | %-11s | %7d | %8d | %10.1f%% | %13.1f%%",
                  Countries[c], TimeToString(earliest,TIME_DATE),
                  n, highCnt, pctAll, pctHigh);

      string joined = "";
      for(int s=0; s<ArraySize(sampleNames); s++)
         joined += (s==0 ? "" : ", ") + sampleNames[s];
      PrintFormat("     high-imp events [%s]: %s", Countries[c], joined);
     }

   Print("=== probe done ===");
  }
//+------------------------------------------------------------------+
