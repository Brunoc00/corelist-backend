import json
import logging
from decimal import Decimal

from google import genai
from pydantic import ValidationError

from apps.lists.schemas import (
    InsightResponseSchema,
    InsightSchema,
)

logger = logging.getLogger(__name__)

GEMINI_MODEL = 'gemini-3.8-flash'


def serialize_context(context):
    return {
        key: (
            str(value)
            if isinstance(value, Decimal)
            else value
        )
        for key, value in context.items()
    }


def build_prompt(context):
    serialized_context = serialize_context(
        context=context,
    )

    return (
        'Você é o assistente de compras do CoreList.\n'
        '\n'
        'Analise somente os fatos fornecidos abaixo.\n'
        'Não invente valores, histórico ou informações.\n'
        'Os cálculos já foram realizados pelo sistema.\n'
        'Não altere nem recalcule os valores.\n'
        '\n'
        'Gere insights úteis, objetivos e em português '
        'do Brasil.\n'
        'Se os dados não sustentarem um insight, '
        'não o invente.\n'
        '\n'
        'Contexto da compra:\n'
        f'{serialized_context}'
    )


def validate_insights(insights):
    valid_insights = []

    for insight in insights:
        try:
            validated_insight = InsightSchema(
                **insight
            )

            valid_insights.append(
                validated_insight.model_dump()
            )

        except ValidationError:
            continue

    return valid_insights


def generate_with_gemini(context):
    try:
        client = genai.Client()

        prompt = build_prompt(
            context=context,
        )

        response = client.interactions.create(
            model=GEMINI_MODEL,
            input=prompt,
            response_format={
                'type': 'text',
                'mime_type': 'application/json',
                'schema': (
                    InsightResponseSchema
                    .model_json_schema()
                ),
            },
        )

        output_text = response.output_text

        if not isinstance(output_text, str):
            return []

        parsed_response = json.loads(
            output_text
        )

        if not isinstance(parsed_response, dict):
            return []

        insights = parsed_response.get(
            'insights',
            [],
        )

        if not isinstance(insights, list):
            return []

        return validate_insights(
            insights=insights,
        )

    except Exception:
        logger.exception(
            'Erro ao gerar insights com Gemini.'
        )

        return []