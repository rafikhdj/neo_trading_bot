"""Moteur synchrone, une stratégie et un journal par compte dédié."""
from dataclasses import dataclass, asdict
from decimal import Decimal as D, ROUND_DOWN, ROUND_UP
import hashlib
import inspect
import math
from pathlib import Path
import time
import uuid

import pandas as pd
from .journal import Journal
from .simulated import Rejected

PENDING = {'prepared', 'sending', 'unknown', 'open', 'partially_filled'}


@dataclass(frozen=True)
class Limits:
    capital: D = D('100')
    per_order: D = D('10')
    daily_loss: D = D('5')
    drawdown: D = D('.1')
    slippage: D = D('.002')
    fee_buffer: D = D('.01')
    candle_age: int = 90
    quote_age: int = 30

    def __post_init__(self):
        for value in asdict(self).values():
            if not D(str(value)).is_finite() or value <= 0:
                raise ValueError('Limites positives et finies requises.')
        if self.drawdown >= 1 or self.slippage >= 1 or self.fee_buffer >= 1:
            raise ValueError('Ratios de risque invalides.')


class Engine:
    def __init__(self, path, broker, strategy, name, limits=None, warmup=30, version=None):
        self.journal = Journal(path)
        self.broker, self.strategy, self.name = broker, strategy, name
        self.limits, self.warmup = limits or Limits(), warmup
        self.source_path = inspect.getsourcefile(strategy)
        self.source_digest = hashlib.sha256(Path(self.source_path).read_bytes()).hexdigest() if self.source_path else None
        self.version = version or hashlib.sha256(inspect.getsource(strategy).encode()).hexdigest()
        self.enabled, self.reason, self.activated_at = False, 'Activation manuelle requise.', None
        self.kill_path = self.journal.path.with_suffix('.kill')
        broker.disarm()
        identity = [name, self.version, self.source_digest, bool(broker.real), broker.account_id, {k: str(v) for k, v in asdict(self.limits).items()}]
        old = self.journal.get('identity')
        self.identity_ok = old is None or old == identity
        if old is None:
            self.journal.put('identity', identity)
        if not self.identity_ok:
            self.reason = 'Stratégie, version ou configuration différente : nouveau journal requis.'
        for o in self.journal.orders():
            if o['status'] == 'prepared':
                self.journal.update(o['id'], 'rejected', reason='Interruption avant envoi ; signal abandonné.')
            elif o['status'] == 'sending':
                self.journal.update(o['id'], 'unknown', reason='Interruption pendant envoi.')

    def stop(self, reason):
        self.enabled = False
        self.broker.disarm()
        self.reason = reason
        try:
            self.journal.put('incident', reason)
        except Exception:
            pass  # L'erreur initiale reste le motif ; aucune nouvelle opération possible.

    def portfolio(self):
        baseline = self.journal.get('baseline')
        if baseline is None:
            raise RuntimeError('Compte non réconcilié.')
        eur, btc, fees = D(baseline['eur']), D(baseline['btc']), D(0)
        sides = {o['id']: o['side'] for o in self.journal.orders()}
        for f in self.journal.fills():
            sign = 1 if sides[f['order_id']] == 'buy' else -1
            eur -= sign * D(f['cost']) + D(f['fee'])
            btc += sign * D(f['quantity'])
            fees += D(f['fee'])
        return {'eur': eur, 'btc': btc, 'fees': fees}

    def valuation(self, bid):
        portfolio = self.portfolio()
        equity = portfolio['eur'] + portfolio['btc'] * D(bid)
        return dict(portfolio, equity=equity, pnl=equity - D(self.journal.get('baseline')['eur']))

    def reconcile(self):
        try:
            if not self.identity_ok:
                raise RuntimeError('Identité du journal incompatible.')
            balances = self.broker.balances()
            if any(not D(v).is_finite() or D(v) < 0 for v in balances.values()):
                raise RuntimeError('Soldes invalides.')
            opens = self.broker.open_orders()
            if len(opens) > 1:
                raise RuntimeError('Plus d’un ordre ouvert.')
            known = {o['id'] for o in self.journal.orders()}
            if any(o['id'] not in known for o in opens):
                raise RuntimeError('Ordre externe détecté.')
            if self.journal.get('baseline') is None:
                if balances['btc'] or opens or self.journal.orders():
                    raise RuntimeError('Premier démarrage : compte dédié initialement FLAT requis.')
                self.journal.put('baseline', {k: str(balances[k]) for k in ('eur', 'btc')})
            for o in self.journal.orders():
                if o['status'] == 'rejected' and not o['txid']:
                    continue
                snapshot = self.broker.find(o['id'], o['txid'])
                if snapshot is None:
                    raise RuntimeError('Ordre introuvable : résultat incertain, aucun réessai.')
                if snapshot['id'] != o['id'] or snapshot['side'] != o['side'] or D(snapshot['quantity']) != D(o['quantity']):
                    raise RuntimeError('Identité de l’ordre incohérente.')
                if snapshot['status'] not in {'open', 'partially_filled', 'filled', 'canceled', 'rejected'}:
                    raise RuntimeError('Statut non confirmé.')
                existing = {f['id'] for f in self.journal.fills() if f['order_id'] == o['id']}
                if not existing.issubset({f['id'] for f in snapshot['fills']}):
                    raise RuntimeError('Historique des exécutions incomplet.')
                quantity = sum((D(f['quantity']) for f in snapshot['fills']), D(0))
                if quantity > D(o['quantity']) or (snapshot['status'] == 'filled' and quantity != D(o['quantity'])):
                    raise RuntimeError('Quantité exécutée incohérente.')
                for f in snapshot['fills']:
                    if any(not D(f[k]).is_finite() or D(f[k]) < 0 for k in ('quantity', 'cost', 'fee')):
                        raise RuntimeError('Exécution invalide.')
                    if D(f['quantity']) <= 0:
                        raise RuntimeError('Exécution vide.')
                    actual_price = D(f['cost']) / D(f['quantity'])
                    if (o['side'] == 'buy' and actual_price > D(o['price'])) or (o['side'] == 'sell' and actual_price < D(o['price'])):
                        raise RuntimeError('Prix exécuté hors limite.')
                self.journal.confirm(o, snapshot)
            # Relire après les ordres pour détecter une exécution survenue pendant la lecture.
            balances = self.broker.balances()
            expected = self.portfolio()
            if abs(expected['eur'] - balances['eur']) > D('.0001') or abs(expected['btc'] - balances['btc']) > D('.00000001'):
                raise RuntimeError('Divergence entre Kraken et le journal.')
            if any(o['status'] in {'sending', 'unknown'} for o in self.journal.orders()):
                raise RuntimeError('Ordre incertain.')
            self.reason = 'Réconciliation OK.'
            return True
        except Exception:
            self.stop('Réconciliation impossible : accès indisponible, divergence ou ordre incertain.')
            return False

    def risk(self, now):
        if self.kill_path.exists():
            raise RuntimeError('Kill switch actif.')
        if not self.identity_ok or (self.source_path and hashlib.sha256(Path(self.source_path).read_bytes()).hexdigest() != self.source_digest):
            raise RuntimeError('Version de stratégie incompatible.')
        quote = self.broker.quote(now)
        if not 0 <= now - quote['time'] <= self.limits.quote_age:
            raise RuntimeError('Cotation périmée.')
        bid, ask = D(quote['bid']), D(quote['ask'])
        if not bid.is_finite() or not ask.is_finite() or not 0 < bid <= ask:
            raise RuntimeError('Cotation invalide.')
        p = self.portfolio()
        initial = D(self.journal.get('baseline')['eur'])
        equity = min(initial, self.limits.capital) + p['eur'] + p['btc'] * bid - initial
        day = time.strftime('%Y-%m-%d', time.gmtime(now))
        stats = self.journal.get('risk', {'day': day, 'start': str(equity), 'peak': str(equity), 'last': str(equity)})
        if stats['day'] != day:
            stats.update(day=day, start=stats['last'])
        peak = max(D(stats['peak']), equity)
        stats.update(peak=str(peak), last=str(equity))
        self.journal.put('risk', stats)
        if D(stats['start']) - equity >= self.limits.daily_loss or equity <= peak * (1 - self.limits.drawdown):
            raise RuntimeError('Limite de perte ou drawdown atteinte.')
        return quote

    def activate(self, confirmation=None, now=None):
        now = time.time() if now is None else now
        if not self.reconcile():
            return False
        try:
            self.risk(now)
            if any(o['status'] in PENDING for o in self.journal.orders()):
                raise RuntimeError('Ordre en cours : surveillance uniquement.')
            self.broker.arm(confirmation)
            self.enabled, self.activated_at, self.reason = True, now, 'Session activée explicitement.'
            return True
        except Exception:
            self.stop('Activation refusée : vérifier risque, kill switch et confirmation.')
            return False

    def cycle(self, data, now=None):
        now = time.time() if now is None else now
        if not self.enabled:
            return None
        try:
            if not self.reconcile():
                return None
            quote = self.risk(now)
            if any(o['status'] in PENDING for o in self.journal.orders()):
                return None
            if len(data) < self.warmup:
                raise RuntimeError('Historique insuffisant.')
            stamps = pd.to_datetime(data['timestamp'], utc=True)
            differences = stamps.diff().dropna().dt.total_seconds()
            if not differences.eq(300).all():
                raise RuntimeError('Historique non continu ou doublonné.')
            values = data[['open', 'high', 'low', 'close', 'volume']].to_numpy(dtype=float)
            if not all(math.isfinite(x) and x >= 0 for x in values.flat):
                raise RuntimeError('Données invalides.')
            if (data[['open', 'high', 'low', 'close']] <= 0).any().any():
                raise RuntimeError('Prix historiques invalides.')
            candle = stamps.iloc[-1].timestamp()
            if candle % 300:
                raise RuntimeError('Clôtures cinq minutes non alignées.')
            closed = candle + 300
            if closed > now or now - closed > self.limits.candle_age:
                raise RuntimeError('Bougie ouverte ou périmée.')
            if closed <= self.activated_at or candle <= self.journal.get('last_candle', -1):
                return None
            # Consommer avant le calcul : une interruption abandonne le signal.
            self.journal.put('last_candle', candle)
            signal = self.strategy(data)
            if signal not in (-1, 0, 1):
                raise RuntimeError('Signal invalide.')
            balances = self.broker.balances()
            rules = self.broker.rules()
            positioned = balances['btc'] >= rules['minimum'] and balances['btc'] * quote['bid'] >= rules['costmin']
            if signal == 0 or (signal == 1 and positioned) or (signal == -1 and not positioned):
                return None
            if any(not D(v).is_finite() or D(v) <= 0 for v in rules.values()):
                raise RuntimeError('Règles de marché invalides.')
            buy = signal == 1
            raw_price = quote['ask'] * (1 + self.limits.slippage) if buy else quote['bid'] * (1 - self.limits.slippage)
            price = (raw_price / rules['tick']).to_integral_value(rounding=ROUND_UP if buy else ROUND_DOWN) * rules['tick']
            budget = min(self.limits.per_order, self.limits.capital - balances['btc'] * quote['ask'], balances['free_eur']) if buy else self.limits.per_order
            quantity = budget / (price * (1 + self.limits.fee_buffer)) if buy else min(balances['free_btc'], budget / price)
            quantity = (quantity / rules['step']).to_integral_value(rounding=ROUND_DOWN) * rules['step']
            if quantity < rules['minimum'] or quantity * price < rules['costmin']:
                raise RuntimeError('Solde insuffisant ou minimum Kraken non atteint.')
            order = {'id': str(uuid.uuid4()), 'candle': str(candle), 'strategy': self.name,
                     'side': 'buy' if buy else 'sell', 'quantity': str(quantity), 'price': str(price)}
            self.journal.prepare(order)
            # Contrôles juste avant l'envoi ; aucune tentative si une lecture échoue.
            self.risk(now)
            fresh = self.broker.balances()
            expected = self.portfolio()
            if (abs(expected['eur'] - fresh['eur']) > D('.0001')
                    or abs(expected['btc'] - fresh['btc']) > D('.00000001')
                    or self.broker.open_orders()):
                self.journal.update(order['id'], 'rejected', reason='Compte modifié avant envoi.')
                raise RuntimeError('Réconciliation requise avant envoi.')
            if (buy and quantity * price * (1 + self.limits.fee_buffer) > fresh['free_eur']) or (not buy and quantity > fresh['free_btc']):
                self.journal.update(order['id'], 'rejected', reason='Solde disponible insuffisant.')
                raise RuntimeError('Solde disponible insuffisant.')
            self.journal.update(order['id'], 'sending')
            try:
                if self.kill_path.exists():
                    raise Rejected('Kill switch actif avant envoi.')
                txid = self.broker.submit(order)
            except Rejected:
                self.journal.update(order['id'], 'rejected', reason='Ordre refusé.')
                self.stop('Ordre refusé ; activation manuelle requise.')
                return order['id']
            except Exception:
                self.stop('Résultat d’envoi incertain ; réconciliation obligatoire.')
                self.journal.update(order['id'], 'unknown', reason='Envoi incertain ; ne pas renvoyer.')
                return order['id']
            self.journal.update(order['id'], 'open', txid=txid)
            self.reconcile()
            return order['id']
        except Exception:
            self.stop('Cycle bloqué : contrôle de risque, données, API ou persistance.')
            return None

    def cancel(self, oid, confirmation=None):
        o = next(o for o in self.journal.orders() if o['id'] == oid)
        if o['status'] not in {'open', 'partially_filled'} or not o['txid']:
            raise RuntimeError('Annulation autorisée uniquement pour un ordre ouvert connu.')
        try:
            self.broker.arm(confirmation)
            self.broker.cancel(o['txid'])
        except Exception:
            self.journal.update(oid, 'unknown', reason='Annulation incertaine.')
            self.stop('Annulation incertaine.')
            return False
        finally:
            self.broker.disarm()
            self.enabled = False
        return self.reconcile()

    def close(self):
        self.broker.disarm()
        self.enabled = False
        self.journal.close()
