<script setup lang="ts">
import { computed, ref } from 'vue';
import { BFormCheckbox, BFormInput } from 'bootstrap-vue-next/components';
import { BIconPlay } from 'bootstrap-icons-vue';
import CodeTextarea from '@/components/CodeTextarea.vue';
import { Queries, type QueryInfo } from '@/plugins/api/spec';
import { toastReject, toastSuccessWith } from '@/plugins/api';
import { useSessionStore } from '@/stores/store-session';
import TwoStageButton from '@/components/TwoStageButton.vue';

const submitted = ref<boolean>(false);
const session = useSessionStore();
type QuerySettings = {
  py_start: number;
  py_end: number | null;
  py_steps: number;
  use_py_filter: boolean;
  use_firstauth_filter: boolean;
};
// const wrappers = Object.values(Wrapper);
const queryInfo = ref<QueryInfo>({
  query: '',
  wrapper: 'scopus', // for now, static and always scopus
  params: {},
});
const settings = ref<QuerySettings>({
  py_start: 2010,
  py_end: null,
  py_steps: 2,
  use_py_filter: true,
  use_firstauth_filter: true,
});

const currentYear = new Date().getFullYear();

// BFormInput emits '' when a number field is cleared; `py_end` uses null for "up to the current year"
const pyEnd = computed<number | '' | null>({
  get: () => settings.value.py_end,
  set: (value) => {
    settings.value.py_end = value === '' ? null : value;
  },
});

const validSettings = computed(
  () =>
    Number.isInteger(settings.value.py_start) &&
    Number.isInteger(settings.value.py_steps) &&
    settings.value.py_steps > 0 &&
    settings.value.py_start < (settings.value.py_end ?? currentYear),
);

function submitQuery() {
  submitted.value = true;
  Queries.submitQueryQueriesPost({
    ...settings.value,
    bodySubmitQueryQueriesPost: { query: queryInfo.value },
  })
    .then((response) => {
      console.log(response.data);
      toastSuccessWith(
        'Submitted query to job queue. It may take a while, but it show up within a few minutes in the queue overview.',
      );
    })
    .catch(toastReject);
}
</script>

<template>
  <section>
    <h1>Submit query</h1>

    <div v-if="session.user?.permission_submit_query" class="mb-3">
      <div class="d-flex justify-content-between align-items-end gap-2 mb-1">
        <label class="form-label small text-body-secondary mb-0" for="test-query-input">
          Query
        </label>
        <a
          class="small"
          href="https://www.elsevier.support/scopus/answer/how-can-i-best-use-the-advanced-search#tips"
          target="_blank"
          rel="noopener noreferrer">
          Scopus advanced search tips
        </a>
      </div>

      <CodeTextarea
        id="test-query-input"
        v-model="queryInfo.query"
        :rows="8"
        autofocus
        placeholder="Enter a query to send to the academic API…" />

      <h4 class="mt-4">Subquery settings</h4>
      <div class="row g-3">
        <div class="col-md-3 d-flex flex-column align-items-baseline">
          <div class="d-flex align-items-end mt-auto">
            <BFormCheckbox
              v-model="settings.use_py_filter"
              switch
              :value="true"
              :unchecked-value="false">
              Generate PY subqueries
            </BFormCheckbox>
          </div>

          <div class="d-flex align-items-end">
            <BFormCheckbox
              v-model="settings.use_firstauth_filter"
              switch
              :value="true"
              :unchecked-value="false">
              Generate FIRSTAUTH subqueries
            </BFormCheckbox>
          </div>
        </div>
        <div class="col-md-3">
          <label class="form-label small text-body-secondary mb-1" for="settings-py-start">
            Start year
          </label>
          <BFormInput
            id="settings-py-start"
            v-model.number="settings.py_start"
            type="number"
            min="1900"
            :max="currentYear - 1" />
        </div>
        <div class="col-md-3">
          <label class="form-label small text-body-secondary mb-1" for="settings-py-end">
            End year
          </label>
          <BFormInput
            id="settings-py-end"
            v-model.number="pyEnd"
            type="number"
            :min="settings.py_start + 1"
            :max="currentYear"
            placeholder="Current year" />
        </div>
        <div class="col-md-3">
          <label class="form-label small text-body-secondary mb-1" for="settings-py-steps">
            Years per step
          </label>
          <BFormInput
            id="settings-py-steps"
            v-model.number="settings.py_steps"
            type="number"
            min="1"
            :max="currentYear - settings.py_start" />
        </div>
      </div>
      <div class="form-text small mb-3">
        Will generate subqueries splitting data by publication year
        <ul>
          <li>for everything published before {{ settings.py_start }},</li>
          <li>for everything published after {{ settings.py_end ?? '[skipped]' }}, and</li>
          <li>
            for publication years between {{ settings.py_start }}–{{ settings.py_end }} in steps of
            {{ settings.py_steps }} year(s).
          </li>
        </ul>
        <div v-if="!validSettings" class="alert alert-warning">
          Check the years and step size above.
        </div>
      </div>

      <TwoStageButton
        variant="primary"
        :aria-disabled="submitted || !validSettings"
        :disabled="submitted || !validSettings"
        @accepted="submitQuery()">
        <BIconPlay />
        Submit
      </TwoStageButton>
    </div>
    <div v-else>You do not have permissions to submit queries.</div>
  </section>
</template>

<style scoped></style>
