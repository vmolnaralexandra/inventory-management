<template>
  <div class="restocking">
    <div class="page-header">
      <h2>{{ t('restocking.title') }}</h2>
      <p>{{ t('restocking.description') }}</p>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else>
      <div v-if="confirmation" class="confirmation-banner">
        <span>{{ t('restocking.orderPlaced', { orderNumber: confirmation.orderNumber, date: confirmation.deliveryDate }) }}</span>
        <router-link to="/orders" class="confirmation-link">{{ t('restocking.viewOrders') }}</router-link>
      </div>
      <div v-if="submitError" class="error">{{ submitError }}</div>

      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.budgetLabel') }}</h3>
          <span class="budget-value">{{ formatMoney(budget) }}</span>
        </div>
        <input
          v-model.number="budget"
          type="range"
          class="budget-slider"
          min="0"
          :max="maxBudget"
          step="100"
          :disabled="maxBudget === 0"
          :aria-label="t('restocking.budgetLabel')"
        />
        <div class="slider-scale">
          <span>{{ formatMoney(0) }}</span>
          <span>{{ formatMoney(maxBudget) }}</span>
        </div>
      </div>

      <div class="stats-grid">
        <div class="stat-card info">
          <div class="stat-label">{{ t('restocking.budget') }}</div>
          <div class="stat-value">{{ formatMoney(budget) }}</div>
        </div>
        <div class="stat-card success">
          <div class="stat-label">{{ t('restocking.recommendedTotal') }}</div>
          <div class="stat-value">{{ formatMoney(totalCost) }}</div>
        </div>
        <div class="stat-card warning">
          <div class="stat-label">{{ t('restocking.remainingBudget') }}</div>
          <div class="stat-value">{{ formatMoney(remainingBudget) }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ t('restocking.itemsAndLeadTime') }}</div>
          <div class="stat-value stat-value-small">
            {{ t('restocking.itemsSummary', { count: recommendations.length, days: longestLeadTime }) }}
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.recommendations') }}</h3>
          <button
            type="button"
            class="place-order-btn"
            :disabled="recommendations.length === 0 || submitting || confirmation !== null"
            @click="placeOrder"
          >
            {{ submitting ? t('restocking.placing') : t('restocking.placeOrder') }}
          </button>
        </div>

        <div v-if="recommendations.length === 0" class="empty-state">
          {{ t('restocking.noRecommendations') }}
        </div>
        <div v-else class="table-container">
          <table>
            <thead>
              <tr>
                <th>{{ t('restocking.table.sku') }}</th>
                <th>{{ t('restocking.table.itemName') }}</th>
                <th>{{ t('restocking.table.trend') }}</th>
                <th>{{ t('restocking.table.quantity') }}</th>
                <th>{{ t('restocking.table.unitCost') }}</th>
                <th>{{ t('restocking.table.lineTotal') }}</th>
                <th>{{ t('restocking.table.leadTime') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="rec in recommendations" :key="rec.sku">
                <td><strong>{{ rec.sku }}</strong></td>
                <td>{{ translateProductName(rec.name) }}</td>
                <td>
                  <span :class="['badge', rec.trend]">{{ t(`trends.${rec.trend}`) }}</span>
                </td>
                <td>
                  {{ rec.quantity }}
                  <span
                    v-if="rec.partial"
                    class="partial-indicator"
                    :title="t('restocking.partialTitle', { quantity: rec.quantity, gap: rec.gap })"
                  >{{ t('restocking.partial') }}</span>
                </td>
                <td>{{ formatMoney(rec.unitCost, 2) }}</td>
                <td><strong>{{ formatMoney(rec.lineTotal, 2) }}</strong></td>
                <td>{{ rec.leadTimeDays }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, computed, watch, onMounted } from 'vue'
import { api } from '../api'
import { useI18n } from '../composables/useI18n'
import { formatCurrency, formatCurrencyWithDecimals } from '../utils/currency'

// Lower rank = allocated first. Unknown trends sort last.
const TREND_RANK = { increasing: 0, stable: 1 }

export default {
  name: 'Restocking',
  setup() {
    const { t, currentCurrency, currentLocale, translateProductName } = useI18n()

    const loading = ref(true)
    const error = ref(null)
    const forecasts = ref([])
    const budget = ref(0)
    const submitting = ref(false)
    const submitError = ref(null)
    const confirmation = ref(null)

    // Budget and costs are in USD (the API currency); formatCurrency converts for display.
    const formatMoney = (amount, decimals = 0) => {
      return decimals > 0
        ? formatCurrencyWithDecimals(amount, currentCurrency.value, decimals)
        : formatCurrency(amount, currentCurrency.value)
    }

    // Candidates: items whose forecasted demand exceeds current demand.
    // Unit cost is held in integer cents so all sums are exact.
    const candidates = computed(() => {
      return forecasts.value
        .map(f => {
          const gap = f.forecasted_demand - f.current_demand
          const unitCents = Math.round(f.unit_cost * 100)
          return {
            sku: f.item_sku,
            name: f.item_name,
            trend: f.trend,
            gap,
            unitCents,
            gapCostCents: gap * unitCents,
            leadTimeDays: f.lead_time_days
          }
        })
        .filter(c => c.gap > 0 && c.unitCents > 0)
    })

    // Slider max: total gap cost of all candidates, rounded up to the next 100 (in dollars).
    const maxBudget = computed(() => {
      const totalCents = candidates.value.reduce((sum, c) => sum + c.gapCostCents, 0)
      return Math.ceil(totalCents / 10000) * 100
    })

    const recommendations = computed(() => {
      // Priority: increasing trend first, then stable; ties broken by larger gap cost.
      const sorted = [...candidates.value].sort((a, b) => {
        const rankA = TREND_RANK[a.trend] ?? 2
        const rankB = TREND_RANK[b.trend] ?? 2
        if (rankA !== rankB) return rankA - rankB
        return b.gapCostCents - a.gapCostCents
      })

      // Greedy allocation in integer cents so the total can never exceed the budget
      // through float rounding (the server rejects total > budget).
      let remainingCents = Math.round(budget.value * 100)
      const result = []
      for (const c of sorted) {
        // Full gap if affordable, otherwise as many whole units as fit.
        const quantity = c.gapCostCents <= remainingCents
          ? c.gap
          : Math.floor(remainingCents / c.unitCents)
        // Skip items we cannot afford even one unit of; cheaper items further down may still fit.
        if (quantity <= 0) continue
        const lineCents = quantity * c.unitCents
        remainingCents -= lineCents
        result.push({
          sku: c.sku,
          name: c.name,
          trend: c.trend,
          gap: c.gap,
          quantity,
          partial: quantity < c.gap,
          unitCost: c.unitCents / 100,
          lineTotal: lineCents / 100,
          lineCents,
          leadTimeDays: c.leadTimeDays
        })
      }
      return result
    })

    const totalCents = computed(() => recommendations.value.reduce((sum, r) => sum + r.lineCents, 0))
    const totalCost = computed(() => totalCents.value / 100)
    const remainingBudget = computed(() => (Math.round(budget.value * 100) - totalCents.value) / 100)
    const longestLeadTime = computed(() => {
      return recommendations.value.reduce((max, r) => Math.max(max, r.leadTimeDays), 0)
    })

    const loadForecasts = async () => {
      try {
        loading.value = true
        error.value = null
        forecasts.value = await api.getDemandForecasts()
        // Default budget: the full max, so every candidate shows on first load.
        // maxBudget is already a multiple of the slider step of 100.
        budget.value = maxBudget.value
      } catch (err) {
        error.value = t('restocking.loadFailed') + ': ' + err.message
      } finally {
        loading.value = false
      }
    }

    const formatDate = (dateString) => {
      const date = new Date(dateString)
      // Guard against missing or malformed dates from the API.
      if (isNaN(date.getTime())) return '-'
      const locale = currentLocale.value === 'ja' ? 'ja-JP' : 'en-US'
      return date.toLocaleDateString(locale, { year: 'numeric', month: 'short', day: 'numeric' })
    }

    const placeOrder = async () => {
      if (recommendations.value.length === 0 || submitting.value) return
      submitting.value = true
      submitError.value = null
      confirmation.value = null
      try {
        const order = await api.createRestockingOrder({
          budget: budget.value,
          items: recommendations.value.map(r => ({ sku: r.sku, quantity: r.quantity }))
        })
        confirmation.value = {
          orderNumber: order.order_number,
          deliveryDate: formatDate(order.expected_delivery)
        }
      } catch (err) {
        // Pydantic 422s return detail as a list of objects; only show plain-string messages
        const detail = err.response?.data?.detail
        submitError.value = typeof detail === 'string' ? detail : t('restocking.orderFailed')
      } finally {
        submitting.value = false
      }
    }

    // Placing an order locks the button so the same plan can't be submitted twice;
    // moving the slider starts a new plan and unlocks it
    watch(budget, () => {
      confirmation.value = null
    })

    onMounted(loadForecasts)

    return {
      t,
      loading,
      error,
      budget,
      maxBudget,
      recommendations,
      totalCost,
      remainingBudget,
      longestLeadTime,
      submitting,
      submitError,
      confirmation,
      formatMoney,
      placeOrder,
      translateProductName
    }
  }
}
</script>

<style scoped>
.budget-value {
  font-size: 1.5rem;
  font-weight: 700;
  color: #0f172a;
}

.budget-slider {
  width: 100%;
  accent-color: #2563eb;
}

.slider-scale {
  display: flex;
  justify-content: space-between;
  font-size: 0.813rem;
  color: #64748b;
  margin-top: 0.25rem;
}

.stat-value-small {
  font-size: 1.5rem;
}

.place-order-btn {
  background: #2563eb;
  color: white;
  border: none;
  border-radius: 6px;
  padding: 0.5rem 1.25rem;
  font-size: 0.875rem;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s ease;
}

.place-order-btn:hover:not(:disabled) {
  background: #1d4ed8;
}

.place-order-btn:disabled {
  background: #cbd5e1;
  color: #64748b;
  cursor: not-allowed;
}

.partial-indicator {
  display: inline-block;
  margin-left: 0.5rem;
  padding: 0.125rem 0.5rem;
  border-radius: 6px;
  background: #fed7aa;
  color: #92400e;
  font-size: 0.688rem;
  font-weight: 600;
  text-transform: uppercase;
}

.empty-state {
  text-align: center;
  padding: 2rem;
  color: #64748b;
  font-size: 0.938rem;
}

.confirmation-banner {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1rem;
  background: #ecfdf5;
  border: 1px solid #a7f3d0;
  color: #065f46;
  padding: 1rem;
  border-radius: 8px;
  margin-bottom: 1.25rem;
  font-size: 0.938rem;
}

.confirmation-link {
  color: #2563eb;
  font-weight: 600;
  text-decoration: none;
  white-space: nowrap;
}

.confirmation-link:hover {
  text-decoration: underline;
}
</style>
