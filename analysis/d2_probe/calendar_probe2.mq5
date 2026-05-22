//+------------------------------------------------------------------+
//|  calendar_probe2.mq5                                             |
//|                                                                  |
//|  D2 data-feasibility probe, v2 -- the TRADEABLE-ROSTER probe.    |
//|                                                                  |
//|  v1 confirmed depth (full to 2023-05) and sample (~1600 pooled). |
//|  v2 settles the two open questions:                              |
//|    (1) Is the low EU/GB consensus % just speech-dilution? It     |
//|        splits high-impact events into NUMERIC (has consensus,    |
//|        = D2-tradeable) vs SPEECH/DECISION (no forecast).         |
//|    (2) Are NFP / US-CPI / European CPI actually present? It      |
//|        prints EVERY distinct high-impact event that carries a    |
//|        consensus, with total/consensus release counts.           |
//|                                                                  |
//|  Read-only. Run as a Script; output -> Toolbox -> Experts tab.   |
//+------------------------------------------------------------------+
#property script_show_inputs
#property strict

input datetime InpFrom = D'2023.05.01 00:00';
input datetime InpTo   = D'2026.05.22 00:00';

string Countries[] = {"US","EU","DE","GB","JP"};

void OnStart()
  {
   PrintFormat("=== D2 tradeable-roster probe  %s -> %s ===",
               TimeToString(InpFrom,TIME_DATE), TimeToString(InpTo,TIME_DATE));

   for(int c=0; c<ArraySize(Countries); c++)
     {
      MqlCalendarValue values[];
      int n = CalendarValueHistory(values, InpFrom, InpTo, Countries[c]);
      if(n <= 0)
        {
         PrintFormat("[%s] no data (err=%d)", Countries[c], GetLastError());
         continue;
        }

      // distinct high-importance events: name, total releases, releases with consensus
      string  evName[];
      int     evTotal[];
      int     evCons[];
      int     speechReleases = 0;   // high-imp releases with NO forecast (speeches/summits)

      for(int i=0; i<n; i++)
        {
         MqlCalendarEvent ev;
         if(!CalendarEventById(values[i].event_id, ev))            continue;
         if(ev.importance != CALENDAR_IMPORTANCE_HIGH)             continue;

         bool hasForecast = (values[i].forecast_value != LONG_MIN);

         // find-or-add by name
         int idx = -1;
         for(int k=0; k<ArraySize(evName); k++)
            if(evName[k] == ev.name) { idx = k; break; }
         if(idx < 0)
           {
            idx = ArraySize(evName);
            ArrayResize(evName,  idx+1);
            ArrayResize(evTotal, idx+1);
            ArrayResize(evCons,  idx+1);
            evName[idx]=ev.name; evTotal[idx]=0; evCons[idx]=0;
           }
         evTotal[idx]++;
         if(hasForecast) evCons[idx]++; else speechReleases++;
        }

      PrintFormat("---- [%s] %d distinct high-imp events; %d speech/no-forecast releases ----",
                  Countries[c], ArraySize(evName), speechReleases);
      // print only the D2-tradeable ones (>=1 consensus-bearing release)
      for(int k=0; k<ArraySize(evName); k++)
        {
         if(evCons[k] <= 0) continue;   // skip pure-speech events here
         PrintFormat("   %4d/%4d cons   %s", evCons[k], evTotal[k], evName[k]);
        }
     }

   Print("=== probe done ===");
  }
//+------------------------------------------------------------------+
